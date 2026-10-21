"""Use real local Git tags and a network-free gh executable for finalization."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('inventory',Path(__file__).with_name('release_inventory.py'))


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        self.m=importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(self.m)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.repo=self.root/'repo'
        self.remote=self.root/'remote.git'
        self.bin=self.root/'bin'
        self.bin.mkdir()
        self.git(None,'init','--bare','-q',str(self.remote))
        self.git(None,'clone','-q',str(self.remote),str(self.repo))
        for key,value in [('user.name','test'),('user.email','test@example.com'),
                          ('commit.gpgsign','false'),('tag.gpgSign','false')]:
            self.git(self.repo,'config',key,value)
        (self.repo/'file').write_text('source')
        self.git(self.repo,'add','file')
        self.git(self.repo,'commit','-qm','B')
        self.subject=self.git(self.repo,'rev-parse','HEAD')
        self.git(self.repo,'tag','-a','v0.10.0','-m','release')
        self.git(self.repo,'push','-q','origin','refs/tags/v0.10.0')
        # A fresh runner has no Git identity: finalize must supply its own tagger.
        self.git(self.repo,'config','--unset','user.name')
        self.git(self.repo,'config','--unset','user.email')
        self.git(self.repo,'config','user.useConfigOnly','true')
        self.inventory=self.repo/'inventory.json'
        self.inventory.write_text('{"source":"B"}')
        (self.bin/'gh').write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
root=pathlib.Path(os.environ['FAKE_GH_ROOT'])
with (root/'calls').open('a') as stream: stream.write(json.dumps(args)+'\\n')
if args[:2]==['release','view']:
 if os.environ.get('RELEASE_EXISTS'):
  print(json.dumps({'tagName':'v0.10.0','isDraft':False,'assets':[{'name':'inventory.json'}]}))
  sys.exit(0)
 sys.exit(1)
if args[0]=='api':
 print('HTTP 404 Not Found')
 sys.exit(1)
if args[:2]==['release','download']:
 directory=pathlib.Path(args[args.index('--dir')+1])
 directory.mkdir(exist_ok=True)
 (directory/'inventory.json').write_text(os.environ.get('REMOTE_INVENTORY','{"source":"B"}'))
''')
        (self.bin/'gh').chmod(0o755)
        self.env=patch.dict(os.environ,{'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
                                      'FAKE_GH_ROOT':str(self.root),
                                      'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def git(self,cwd,*args):
        return subprocess.check_output(['git',*args],cwd=cwd,text=True,stderr=subprocess.DEVNULL).strip()

    def finalize(self):
        with contextlib.chdir(self.repo):
            self.m.finalize('SkunkWerkx/HyperCast','0.10.0',self.subject,self.inventory)

    def test_creates_go_tag_at_b_and_idempotently_creates_release(self):
        self.finalize()
        self.assertEqual(self.git(self.remote,'rev-parse','go/v0.10.0^{}'),self.subject)
        os.environ['RELEASE_EXISTS']='1'
        self.addCleanup(os.environ.pop,'RELEASE_EXISTS',None)
        self.finalize()
        calls=[json.loads(line) for line in (self.root/'calls').read_text().splitlines()]
        self.assertEqual(sum(call[:2]==['release','create'] for call in calls),1)

    def test_rejects_existing_go_tag_with_another_subject(self):
        (self.repo/'file').write_text('unrelated')
        self.git(self.repo,'-c','user.name=test','-c','user.email=test@example.com','commit','-am','other','-q')
        self.git(self.repo,'tag','go/v0.10.0')
        self.git(self.repo,'push','-q','origin','refs/tags/go/v0.10.0')
        with self.assertRaisesRegex(ValueError,'Go tag'):
            self.finalize()

    def test_rejects_conflicting_existing_release_inventory(self):
        os.environ['RELEASE_EXISTS']='1'
        os.environ['REMOTE_INVENTORY']='different bytes'
        self.addCleanup(os.environ.pop,'RELEASE_EXISTS',None)
        self.addCleanup(os.environ.pop,'REMOTE_INVENTORY',None)
        with self.assertRaisesRegex(ValueError,'inventory'):
            self.finalize()


if __name__=='__main__': unittest.main()
