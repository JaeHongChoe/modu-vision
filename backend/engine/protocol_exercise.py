"""Short, owned loopback receiver sessions exercising the real protocol adapters."""
import json
import socketserver
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from backend.engine.field_adapters import HTTPMESAdapter,HTTPMESConfig,ModbusTCPAdapter,ModbusConfig


def exercise_protocol(protocol,mode='success'):
    if protocol not in ('http','modbus') or mode not in ('success','reject','timeout'):raise ValueError('Select HTTP/Modbus and success/reject/timeout')
    captured={};payload={'job_id':'local-contract-check','model_verdict':'NG','image_sha256':'0'*64,'result':{}}
    if protocol=='http':
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));captured.update(data)
                if mode=='timeout':time.sleep(.25)
                result={'accepted':mode=='success','job_id':data['job_id']};self.send_response(200);self.end_headers()
                try:self.wfile.write(json.dumps(result).encode())
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        adapter=HTTPMESAdapter(HTTPMESConfig(url=f'http://127.0.0.1:{server.server_port}/check',timeout=.1))
    else:
        registers={10:0,11:0,12:0}
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                header=ModbusTCPAdapter._receive(self.request,7);transaction,protocol_id,length,unit=struct.unpack('>HHHB',header)
                body=ModbusTCPAdapter._receive(self.request,length-1);function,address,value=struct.unpack('>BHH',body)
                if function==6:
                    registers[address]=value
                    if address==12:registers[11]=value if mode=='success' else 0
                    response=body
                else:
                    response=struct.pack('>BBH',3,2,registers.get(address,0))
                    if mode=='reject':response=bytes([131,2])
                    if mode=='timeout':time.sleep(.2)
                self.request.sendall(struct.pack('>HHHB',transaction,protocol_id,len(response)+1,unit)+response)
        class Server(socketserver.ThreadingTCPServer):daemon_threads=True;allow_reuse_address=True
        server=Server(('127.0.0.1',0),Handler)
        adapter=ModbusTCPAdapter(ModbusConfig(host='127.0.0.1',port=server.server_address[1],result_register=10,ack_register=11,sequence_register=12,timeout=.1,ack_timeout=.15))
    worker=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);worker.start()
    try:
        try:receipt=adapter.deliver(payload);ack=True;error=None
        except Exception as exc:receipt=None;ack=False;error=type(exc).__name__+': '+str(exc)
        return {'protocol':protocol,'mode':mode,'acknowledged':ack,'receipt':receipt,'error':error,
                'received_job_id':captured.get('job_id'),'scope':'loopback_contract_only','physical_equipment_verified':False}
    finally:server.shutdown();server.server_close();worker.join(1)
