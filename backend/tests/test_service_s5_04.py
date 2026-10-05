"""Bounded real loopback ACK behavior; no physical PLC/MES claim."""
import json
from http.server import BaseHTTPRequestHandler,HTTPServer
import socketserver
import struct
import threading
import pytest
from backend.engine.field_adapters import HTTPMESConfig,HTTPMESAdapter,ModbusConfig,ModbusTCPAdapter

@pytest.mark.parametrize('options',[{'ack_field':None,'ack_job_field':None},{'ack_field':'accepted','ack_job_field':None},{'ack_field':None,'ack_job_field':'job_id'}])
def test_mes_requires_positive_and_same_job_ack_before_contact(options):
 with pytest.raises(ValueError,match='ack|ACK'):HTTPMESConfig(url='http://127.0.0.1:1',**options)

@pytest.mark.parametrize('answer',[{'accepted':True,'job_id':'owned-job'},{'accepted':1,'job_id':'owned-job'},{'accepted':True,'job_id':'foreign-job'}])
def test_http_idempotent_identity_and_typed_ack_on_actual_socket(answer):
 observed=[]
 class Handler(BaseHTTPRequestHandler):
  def do_POST(self):
   payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));observed.append((payload,self.headers.get('Idempotency-Key')))
   encoded=json.dumps(answer).encode();self.send_response(200);self.end_headers();self.wfile.write(encoded)
  def log_message(self,*args):pass
 server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  adapter=HTTPMESAdapter(HTTPMESConfig(url=f'http://127.0.0.1:{server.server_port}',field_mapping={'receipt':'job_id','decision':'model_verdict'}))
  payload={'job_id':'owned-job','model_verdict':'NG'}
  if type(answer['accepted']) is bool and answer['job_id']=='owned-job':assert adapter.deliver(payload)['acknowledged']
  else:
   with pytest.raises(ValueError,match='acknowledg'):adapter.deliver(payload)
  assert observed==[({'receipt':'owned-job','decision':'NG'},'owned-job')]
 finally:server.shutdown();server.server_close();thread.join()

@pytest.mark.parametrize('byte_order,wire_value',[('big',0x1234),('little',0x3412)])
def test_modbus_16_bit_payload_order_and_ack_on_actual_socket(byte_order,wire_value):
 registers={10:0,11:0};writes=[]
 class Handler(socketserver.BaseRequestHandler):
  def handle(self):
   header=ModbusTCPAdapter._receive(self.request,7);transaction,protocol,length,unit=struct.unpack('>HHHB',header)
   body=ModbusTCPAdapter._receive(self.request,length-1);function,address,value=struct.unpack('>BHH',body)
   if function==6:
    writes.append((address,value));registers[address]=value
    if address==10:registers[11]=value
    response=body
   else:response=struct.pack('>BBH',3,2,registers[address])
   self.request.sendall(struct.pack('>HHHB',transaction,0,len(response)+1,unit)+response)
 server=socketserver.TCPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  adapter=ModbusTCPAdapter(ModbusConfig(host='127.0.0.1',port=server.server_address[1],result_register=10,ack_register=11,byte_order=byte_order,verdict_values={'OK':0x1234,'NG':2,'REVIEW':3},timeout=.2,ack_timeout=.2))
  assert adapter.deliver({'job_id':'owned-job','model_verdict':'OK'})['acknowledged'];assert writes==[(11,0),(10,wire_value)]
 finally:server.shutdown();server.server_close();thread.join()

def test_trigger_path_is_confined_to_project_source_before_configuration(tmp_path):
 from fastapi import FastAPI
 from backend.tests.test_product_delivery import project,ApiClient
 from backend.api.routes_runtime_services import router
 from PIL import Image
 p=project(tmp_path);external=tmp_path/'outside.png';Image.new('RGB',(8,8)).save(external)
 app=FastAPI();app.include_router(router)
 @app.middleware('http')
 async def scope(request,next):request.state.scoped_project=p;return await next(request)
 client=ApiClient(app);body={'enabled':False,'modbus':{'host':'127.0.0.1','result_register':10,'ack_register':11,'trigger_register':12,'trigger_image_path':str(external)}}
 reply=client.request('PUT','/api/runtime-services/adapters',json=body)
 assert reply.status_code==409 and not (tmp_path/'project/runtime_service/adapters.json').exists()
 source=tmp_path/'source/owned.png';Image.new('RGB',(8,8)).save(source);body['modbus']['trigger_image_path']=str(source)
 reply=client.request('PUT','/api/runtime-services/adapters',json=body);assert reply.status_code==200,reply.text
 assert client.get('/api/runtime-services').json()['adapter_config']['modbus']['trigger_image_path']==str(source)
