#!/usr/bin/env python3
# This opt-in operation is separate from setup because it writes synthetic data
# and changes the gateway demo connection. The selected context must be correct;
# the MongoDB tunnel and demo reader credentials are implementation details, not
# values to print or commit.

"""Import isolated, labelled synthetic data using a temporary localhost Mongo tunnel."""

# Portable operator command; imports remain usable by isolated tests.
if __name__ == '__main__':
    import sys as _host_sys
    if not any(arg in ('--help', '-h') for arg in _host_sys.argv[1:]):
        import subprocess as _host_subprocess
        from pathlib import Path as _HostPath
        _host_gate = _HostPath(__file__).resolve().parent / '../..' / 'scripts/lib/require-centos.sh'
        if _host_subprocess.call(['bash', str(_host_gate)]):
            raise SystemExit(1)

import argparse
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--apply', action='store_true', help='Explicitly seed labelled demo data and update gateway configuration; requires pymongo, kubectl and KUBECONFIG/KUBE_CONTEXT')
args = parser.parse_args()
if not args.apply:
    parser.error('Seeding requires explicit --apply')
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
DATABASE = 'roboshop_demo_merchant20'
DATASET = 'merchant20-1000-v1'


def run(args, payload=None):
    result = subprocess.run(args, input=payload, text=True, capture_output=True, timeout=100)
    if result.returncode: raise RuntimeError('Kubernetes operation failed. Check context, connectivity and permissions; credentials withheld.')
    return result.stdout


def main():
    if not os.getenv('KUBECONFIG'):
        config=Path.home()/'.kube/vultr-minikube.json'
        if config.is_file(): os.environ['KUBECONFIG']=str(config)
    k=['kubectl','--request-timeout=30s']
    if os.getenv('KUBE_CONTEXT'): k += ['--context',os.environ['KUBE_CONTEXT']]
    k += ['-n','roboshop-ai']
    os.environ['APP_ENV']='demo'
    sys.path.insert(0,str(ROOT/'scripts/lib'))
    from repos import APP_REPO, checkout
    spec=importlib.util.spec_from_file_location('demo',checkout(APP_REPO)/'services/catalogue-service/scripts/seed-demo.py')
    demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)
    demo.guard(DATABASE)
    secret=json.loads(run(k+['get','secret','roboshop-secrets','-o','json']))
    root_password=base64.b64decode(secret['data']['MONGO_ROOT_PASSWORD']).decode()
    previous=secret.get('data',{}).get('DEMO_MONGODB_URL')
    password=secrets.token_hex(32)
    if previous:
        parsed=urlparse(base64.b64decode(previous).decode())
        if parsed.username!='roboshop_demo_reader' or parsed.hostname!='mongodb' or parsed.path not in ('/'+DATABASE,'/roboshop_demo_global'):
            raise ValueError('Existing demo connection is managed elsewhere; refusing to replace it.')
        password=parsed.password
    uri=f'mongodb://roboshop_demo_reader:{password}@mongodb:27017/{DATABASE}?authSource={DATABASE}'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    forward=subprocess.Popen(k+['port-forward','--address=127.0.0.1','deployment/mongodb',f'{port}:27017'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(300):
            if forward.poll() is not None: raise RuntimeError('Mongo port-forward failed. Check cluster connectivity.')
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2): break
            except OSError: time.sleep(.1)
        else: raise RuntimeError('Mongo port-forward did not become ready.')
        with MongoClient('127.0.0.1',port,username='root',password=root_password,authSource='admin',directConnection=True,serverSelectionTimeoutMS=10000,socketTimeoutMS=30000) as client:
            client.admin.command('ping')
            target=client[DATABASE]
            if target.demo_profiles.count_documents({'datasetId':{'$ne':DATASET}}):
                raise ValueError('Another dataset exists in the demo database; no data removed.')
            print('Connected. Seeding isolated synthetic profiles and reviews...',flush=True)
            counts=demo.seed(target,DATASET,demo.catalog_products(),customers=1000,reviews=3000)
            if target.command('usersInfo','roboshop_demo_reader')['users']:
                target.command('updateUser','roboshop_demo_reader',pwd=password,roles=[{'role':'read','db':DATABASE}])
            else:
                target.command('createUser','roboshop_demo_reader',pwd=password,roles=[{'role':'read','db':DATABASE}])
            actual={'profiles':target.demo_profiles.count_documents({'datasetId':DATASET}),'reviews':target.reviews.count_documents({'datasetId':DATASET}),'countries':len(target.demo_profiles.distinct('country',{'datasetId':DATASET}))}
            if actual!={'profiles':1000,'reviews':3000,'countries':200}: raise RuntimeError('Import verification failed; rerun the same command to resume.')
            print(json.dumps(actual),flush=True)
    finally:
        forward.terminate()
        try: forward.wait(timeout=5)
        except subprocess.TimeoutExpired: forward.kill();forward.wait()
    # Merge only this key; preserve all database, payment, MFA and SES credentials.
    run(k+['patch','secret','roboshop-secrets','--type=merge','--patch-file=/dev/stdin'],json.dumps({'stringData':{'DEMO_MONGODB_URL':uri}}))
    run(k+['set','env','deployment/ai-gateway','DEMO_REVIEWS_DATABASE='+DATABASE])
    run(k+['rollout','restart','deployment/ai-gateway'])
    run(k+['rollout','status','deployment/ai-gateway','--timeout=90s'])
    print('Verified demo import and gateway rollout. Real customer data was not changed.')

if __name__=='__main__':
    try: main()
    except Exception as error:
        print(str(error) if isinstance(error,(ValueError,RuntimeError)) else 'Demo import failed ('+type(error).__name__+'); credentials withheld. Check MongoDB readiness, cluster connectivity and retry.',flush=True)
        raise SystemExit(1)
