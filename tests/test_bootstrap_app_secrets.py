import base64
import importlib.util
import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
import pytest

spec=importlib.util.spec_from_file_location('app_secrets',Path(__file__).resolve().parents[1]/'scripts/argocd/bootstrap.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_fresh_secret_has_required_keys_and_valid_fernet_key(capsys):
    with patch.object(m.subprocess,'check_call'), patch.object(m.subprocess,'check_output',return_value=''), patch.object(m.subprocess,'run',return_value=CompletedProcess([],0)) as write:
        m.ensure_app_secrets('mk4')
    doc=json.loads(write.call_args.kwargs['input']); values=doc['stringData']
    assert set(m.INTERNAL_PASSWORD_KEYS) <= values.keys()
    assert values['SUPERADMIN_EMAIL']=='team@jithendar.com'
    assert len(base64.urlsafe_b64decode(values['AUTH_ENCRYPTION_KEY']))==32
    assert len(set(values[k] for k in m.INTERNAL_PASSWORD_KEYS))==len(m.INTERNAL_PASSWORD_KEYS)
    output=capsys.readouterr().out
    assert all(values[k] not in output for k in m.INTERNAL_PASSWORD_KEYS)
    assert 'create' in write.call_args.args[0]
    assert 'SUPERADMIN_PASSWORD' not in ' '.join(write.call_args.args[0])


def test_existing_credentials_not_changed():
    values={k:'preserved' for k in m.INTERNAL_PASSWORD_KEYS+('AUTH_ENCRYPTION_KEY','SUPERADMIN_EMAIL')}
    with patch.object(m.subprocess,'check_call'), patch.object(m.subprocess,'check_output',return_value=json.dumps({'data':values})), patch.object(m.subprocess,'run') as write:
        m.ensure_app_secrets('mk4')
    write.assert_not_called()


def test_partial_secret_not_silently_rotated():
    with patch.object(m.subprocess,'check_call'), patch.object(m.subprocess,'check_output',return_value='{"data":{"MONGO_APP_PASSWORD":"keep"}}'), patch.object(m.subprocess,'run') as write:
        with pytest.raises(RuntimeError,match='preserved'):
            m.ensure_app_secrets('mk4')
    write.assert_not_called()
