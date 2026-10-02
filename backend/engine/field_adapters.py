"""Configurable generic field protocols; delivery needs an explicit acknowledgment."""
from __future__ import annotations
import json
import socket
import struct
import time
import uuid
from pathlib import Path
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModbusConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    host:str=Field(min_length=1)
    port:int=Field(default=502,ge=1,le=65535)
    unit_id:int=Field(default=1,ge=0,le=255)
    result_register:int=Field(ge=0,le=65535)
    ack_register:int=Field(ge=0,le=65535)
    sequence_register:int|None=Field(default=None,ge=0,le=65535)
    trigger_register:int|None=Field(default=None,ge=0,le=65535)
    trigger_image_path:str|None=None
    timeout:float=Field(default=2,gt=0,le=30)
    ack_timeout:float=Field(default=5,gt=0,le=60)
    verdict_values:dict[str,int]=Field(default_factory=lambda:{'OK':1,'NG':2,'REVIEW':3})

    @model_validator(mode='after')
    def distinct_signals(self):
        addresses=[address for address in (self.result_register,self.ack_register,self.sequence_register,self.trigger_register) if address is not None]
        if len(set(addresses))!=len(addresses):
            raise ValueError('Modbus result, acknowledgment, sequence and trigger registers must be distinct')
        if self.sequence_register is None and any(value==0 for value in self.verdict_values.values()):
            raise ValueError('A zero verdict code requires a separate sequence acknowledgment register')
        return self


class ModbusTCPAdapter:
    def __init__(self,config:ModbusConfig):
        self.config=config
        if set(config.verdict_values)!={'OK','NG','REVIEW'} or any(type(v) is not int or not 0<=v<=65535 for v in config.verdict_values.values()):
            raise ValueError('Verdict register mapping needs OK/NG/REVIEW unsigned values')
    @staticmethod
    def _receive(connection,length):
        data=b''
        while len(data)<length:
            chunk=connection.recv(length-len(data))
            if not chunk: raise ConnectionError('Modbus connection closed')
            data+=chunk
        return data
    def _request(self,function,address,value):
        transaction=uuid.uuid4().int&65535
        body=struct.pack('>BHH',function,address,value)
        with socket.create_connection((self.config.host,self.config.port),timeout=self.config.timeout) as connection:
            connection.settimeout(self.config.timeout)
            connection.sendall(struct.pack('>HHHB',transaction,0,len(body)+1,self.config.unit_id)+body)
            received,protocol,length,unit=struct.unpack('>HHHB',self._receive(connection,7))
            if received!=transaction or protocol!=0 or unit!=self.config.unit_id or not 2<=length<=254:
                raise ValueError('Invalid Modbus response identity')
            response=self._receive(connection,length-1)
            if response[0]==(function|128): raise RuntimeError(f'Modbus exception {response[1]}')
            if response[0]!=function: raise ValueError('Unexpected Modbus function')
            return response
    def read_register(self,address):
        response=self._request(3,address,1)
        if len(response)!=4 or response[1]!=2: raise ValueError('Invalid Modbus register response')
        return struct.unpack('>H',response[2:])[0]
    def write_register(self,address,value):
        response=self._request(6,address,value)
        if response!=struct.pack('>BHH',6,address,value): raise ValueError('Modbus write was not echoed')
    def deliver(self,payload):
        verdict=payload.get('model_verdict')
        if verdict not in self.config.verdict_values: raise ValueError('Invalid verdict for Modbus delivery')
        value=self.config.verdict_values[verdict]
        expected=value
        if self.config.sequence_register is not None:
            import hashlib
            expected=int(hashlib.sha256(payload['job_id'].encode()).hexdigest()[:4],16) or 1
            # Clear prior ack before writing the result so stale registers cannot approve a new job.
            self.write_register(self.config.ack_register,0)
            self.write_register(self.config.result_register,value)
            self.write_register(self.config.sequence_register,expected)
        else:
            self.write_register(self.config.ack_register,0)
            self.write_register(self.config.result_register,value)
        deadline=time.monotonic()+self.config.ack_timeout
        while time.monotonic()<deadline:
            if self.read_register(self.config.ack_register)==expected: return {'acknowledged':True,'register_value':expected}
            time.sleep(min(.05,self.config.ack_timeout))
        raise TimeoutError('Modbus acknowledgment timed out')


class HTTPMESConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    url:str
    timeout:float=Field(default=5,gt=0,le=60)
    token:str|None=None
    field_mapping:dict[str,str]=Field(default_factory=lambda:{'job_id':'job_id','model_verdict':'model_verdict','image_sha256':'image_sha256','result':'result'})
    ack_field:str|None='accepted'
    ack_value:bool|str|int=True
    ack_job_field:str|None='job_id'


class HTTPMESAdapter:
    def __init__(self,config:HTTPMESConfig):
        if not config.url.startswith(('http://','https://')):raise ValueError('MES URL must be HTTP(S)')
        self.config=config
    @staticmethod
    def lookup(payload,path):
        value=payload
        for component in path.split('.'):
            if not isinstance(value,dict) or component not in value:raise ValueError(f'MES mapping is missing {path}')
            value=value[component]
        return value
    def deliver(self,payload):
        mapped={field:self.lookup(payload,path) for field,path in self.config.field_mapping.items()}
        response=httpx.post(self.config.url,json=mapped,headers={'Authorization':f'Bearer {self.config.token}'} if self.config.token else {},timeout=self.config.timeout)
        response.raise_for_status()
        if self.config.ack_field or self.config.ack_job_field:
            acknowledgment=response.json()
            if self.config.ack_field and self.lookup(acknowledgment,self.config.ack_field)!=self.config.ack_value:
                raise ValueError('MES result was not acknowledged')
            if self.config.ack_job_field and self.lookup(acknowledgment,self.config.ack_job_field)!=payload['job_id']:
                raise ValueError('MES acknowledgment belongs to another job')
        return {'acknowledged':True,'http_status':response.status_code}


class FieldAdapterConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=False
    modbus:ModbusConfig|None=None
    mes:HTTPMESConfig|None=None
    clear_mes_token:bool=False


def load_adapter_config(path:Path|None):
    config=FieldAdapterConfig.model_validate_json(path.read_text(encoding='utf-8')) if path else FieldAdapterConfig()
    if config.modbus:ModbusTCPAdapter(config.modbus)
    if config.mes:HTTPMESAdapter(config.mes)
    return config


def deliver_configured(config,payload):
    if not config.enabled:return
    if config.modbus:ModbusTCPAdapter(config.modbus).deliver(payload)
    if config.mes:HTTPMESAdapter(config.mes).deliver(payload)
