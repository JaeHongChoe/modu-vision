import json
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest


@pytest.mark.parametrize('registers', [
    {'result_register':10,'ack_register':10},
    {'result_register':10,'ack_register':11,'sequence_register':11},
    {'result_register':10,'ack_register':11,'sequence_register':10},
    {'result_register':10,'ack_register':11,'trigger_register':10},
])
def test_modbus_signal_registers_must_be_distinct_without_contacting_hardware(registers,monkeypatch):
    from backend.engine.field_adapters import ModbusConfig
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *args,**kwargs:pytest.fail('Invalid signal configuration must not contact equipment'))
    with pytest.raises(ValueError,match='distinct'):ModbusConfig(host='127.0.0.1',**registers)


def test_modbus_loopback_read_write_and_ack():
    from backend.engine.field_adapters import ModbusTCPAdapter, ModbusConfig
    registers={10:0,11:0}
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            header=self.request.recv(7)
            transaction,protocol,length,unit=struct.unpack('>HHHB',header)
            body=self.request.recv(length-1)
            function,address,value=struct.unpack('>BHH',body)
            if function==6:
                registers[address]=value; registers[11]=value; response=body
            else: response=struct.pack('>BBH',3,2,registers[address])
            self.request.sendall(struct.pack('>HHHB',transaction,0,len(response)+1,unit)+response)
    server=socketserver.TCPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
    try:
        adapter=ModbusTCPAdapter(ModbusConfig(host='127.0.0.1',port=server.server_address[1],result_register=10,ack_register=11,timeout=.5,ack_timeout=.5))
        assert adapter.read_register(10)==0
        adapter.deliver({'job_id':'one','model_verdict':'NG'})
        assert registers[10]==2
    finally: server.shutdown();server.server_close();worker.join()


def test_modbus_wrong_ack_raises_instead_of_accepting_verdict(monkeypatch):
    from backend.engine.field_adapters import ModbusTCPAdapter, ModbusConfig
    adapter=ModbusTCPAdapter(ModbusConfig(host='127.0.0.1',port=1,result_register=10,ack_register=11,timeout=.02,ack_timeout=.02))
    monkeypatch.setattr(adapter,'write_register',lambda *args:None)
    monkeypatch.setattr(adapter,'read_register',lambda *args:0)
    with pytest.raises(TimeoutError,match='acknowledgment'): adapter.deliver({'job_id':'one','model_verdict':'OK'})


def test_http_mes_mapping_requires_matching_job_ack():
    from backend.engine.field_adapters import HTTPMESAdapter, HTTPMESConfig
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length']))); observed.append(body)
            response=json.dumps({'accepted':True,'job_id':body['inspection']}).encode()
            self.send_response(200);self.end_headers();self.wfile.write(response)
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        adapter=HTTPMESAdapter(HTTPMESConfig(url=f'http://127.0.0.1:{server.server_port}',field_mapping={'inspection':'job_id','decision':'model_verdict'},ack_field='accepted',ack_job_field='job_id'))
        adapter.deliver({'job_id':'inspection-1','model_verdict':'NG'})
        assert observed==[{'inspection':'inspection-1','decision':'NG'}]
    finally:server.shutdown();server.server_close();worker.join()
