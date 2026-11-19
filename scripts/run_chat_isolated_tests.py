"""Run tests with a fresh import directory and an enforced loopback-only network."""
import ipaddress
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))


def local(host):
    if host in ('localhost',None,''):return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False


def guard(event,args):
    if event=='socket.getaddrinfo' and not local(args[0]):
        raise RuntimeError('Isolation guard denied non-loopback DNS')
    if event=='socket.connect' and isinstance(args[1],tuple) and not local(args[1][0]):
        raise RuntimeError('Isolation guard denied non-loopback connection')


if __name__=='__main__':
    sys.addaudithook(guard)
    for key in list(os.environ):
        if any(part in key.upper() for part in ('API_KEY','ACCESS_TOKEN','SECRET_KEY')):
            os.environ.pop(key,None)
    os.environ.update(YOSAKI_BACKGROUND_TASKS_ENABLED='0',YOSAKI_CHRONICLE_PAID_ENABLED='0')
    with tempfile.TemporaryDirectory(prefix='yosaki-chat-test-import-') as temp:
        os.environ['YOSAKI_DATA_DIR']=temp
        suites=[unittest.defaultTestLoader.loadTestsFromName(p[5:]) if p.startswith('case:') else unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=p) for p in (sys.argv[1:] or ['test_chat_enhancement.py'])]
        result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(suites))
        raise SystemExit(not result.wasSuccessful())
