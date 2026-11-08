"""Optional real local collector test. No external exporter or infrastructure."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
import yaml
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.getenv('OTELCOL'),'Set OTELCOL to a local collector 0.162.0 binary')
class CollectorTracing(unittest.TestCase):
 def test_successful_probes_filtered_errors_and_products_retained(self):
  config=yaml.safe_load((ROOT/'deploy/minikube/observability/otel-collector/collector.yaml').read_text())
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
  config['receivers']['otlp']['protocols']['http']['endpoint']=f'127.0.0.1:{port}'
  config['exporters']={'debug':{'verbosity':'detailed'}}
  config['service']['pipelines']['traces']['exporters']=['debug']
  config['processors']['batch']['timeout']='100ms'
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'config.yaml';path.write_text(yaml.safe_dump(config))
   with (Path(directory)/'log').open('w+') as log:
    process=subprocess.Popen([os.environ['OTELCOL'],'--config='+str(path)],stdout=log,stderr=log)
    try:
     for _ in range(50):
      try:
       with socket.create_connection(('127.0.0.1',port),timeout=.1):break
      except OSError:time.sleep(.1)
     now=str(time.time_ns());spans=[]
     for n,(name,route,status) in enumerate([('successful-probe','/health',200),('failed-probe','/health',503),('product-read','/api/products',200),('unmatched-error','',404)]):
      spans.append({'traceId':'a'*32,'spanId':f'{n+1:016x}','name':name,'kind':2,'startTimeUnixNano':now,'endTimeUnixNano':now,'status':{'code':2 if status>=500 else 0},'attributes':[{'key':'http.route','value':{'stringValue':route}},{'key':'http.response.status_code','value':{'intValue':str(status)}},{'key':'db.statement','value':{'stringValue':'PRIVATE_SQL'}}],'events':[{'name':'exception','timeUnixNano':now,'attributes':[{'key':'exception.message','value':{'stringValue':'PRIVATE_EXCEPTION'}}]}]})
     payload={'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'test'}}]},'scopeSpans':[{'scope':{'name':'synthetic-test'},'spans':spans}]}]}
     req=urllib.request.Request(f'http://127.0.0.1:{port}/v1/traces',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
     with urllib.request.urlopen(req,timeout=3) as response:self.assertEqual(response.status,200)
     time.sleep(.6)
    finally:
     process.terminate();process.wait(timeout=5)
    log.seek(0);output=log.read()
   self.assertNotIn('Name           : successful-probe',output)
   self.assertIn('failed-probe',output);self.assertIn('product-read',output);self.assertIn('unmatched-error',output)
   self.assertNotIn('PRIVATE_SQL',output);self.assertNotIn('PRIVATE_EXCEPTION',output)
