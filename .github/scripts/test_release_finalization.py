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
  names=json.loads(os.environ.get('RELEASE_ASSETS','["inventory.json"]'))
  print(json.dumps({'tagName':'v0.10.0','isDraft':False,'assets':[{'name':name} for name in names]}))
  sys.exit(0)
 sys.exit(1)
if args[0]=='api':
 print('HTTP 404 Not Found')
 sys.exit(1)
if args[:2]==['release','download']:
 directory=pathlib.Path(args[args.index('--dir')+1])
 directory.mkdir(exist_ok=True)
 name=args[args.index('--pattern')+1]
 remote=json.loads(os.environ.get('REMOTE_ASSETS','{}'))
 (directory/name).write_text(remote.get(name,os.environ.get('REMOTE_INVENTORY','{"source":"B"}')))
''')
        (self.bin/'gh').chmod(0o755)
        self.env=patch.dict(os.environ,{'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
                                      'FAKE_GH_ROOT':str(self.root),
                                      'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def git(self,cwd,*args):
        return subprocess.check_output(['git',*args],cwd=cwd,text=True,stderr=subprocess.DEVNULL).strip()

    def finalize(self, notes=None):
        with contextlib.chdir(self.repo):
            self.m.finalize('SkunkWerkx/HyperCast','0.10.0',self.subject,self.inventory,notes)

    def test_release_body_comes_from_the_notes_file_when_given(self):
        notes=self.repo/'notes.md'
        notes.write_text('body')
        self.finalize(notes)
        calls=[json.loads(line) for line in (self.root/'calls').read_text().splitlines()]
        create=next(call for call in calls if call[:2]==['release','create'])
        self.assertIn('--notes-file',create)
        self.assertEqual(create[create.index('--notes-file')+1],str(notes))
        self.assertNotIn('--generate-notes',create)
        self.assertEqual(create[-1],str(self.inventory))

    def test_creates_go_tag_at_b_and_idempotently_creates_release(self):
        self.finalize()
        self.assertEqual(self.git(self.remote,'rev-parse','go/v0.10.0^{}'),self.subject)
        os.environ['RELEASE_EXISTS']='1'
        self.addCleanup(os.environ.pop,'RELEASE_EXISTS',None)
        self.finalize()
        calls=[json.loads(line) for line in (self.root/'calls').read_text().splitlines()]
        self.assertEqual(sum(call[:2]==['release','create'] for call in calls),1)
        self.assertIn('--generate-notes',next(call for call in calls if call[:2]==['release','create']))

    def test_rejects_existing_go_tag_with_another_subject(self):
        (self.repo/'file').write_text('unrelated')
        self.git(self.repo,'-c','user.name=test','-c','user.email=test@example.com','commit','-am','other','-q')
        self.git(self.repo,'tag','go/v0.10.0')
        self.git(self.repo,'push','-q','origin','refs/tags/go/v0.10.0')
        with self.assertRaisesRegex(ValueError,'Go tag'):
            self.finalize()

    def github_assets(self):
        github=self.repo/'github'
        github.mkdir()
        for name in ('HyperUuidCore.artifactbundle.zip','HyperUuidCoreApple.xcframework.zip'):
            (github/name).write_text(name)
        return sorted(github.iterdir())

    def test_a_new_release_carries_every_github_asset_beside_the_inventory(self):
        assets=self.github_assets()
        self.finalize()
        calls=[json.loads(line) for line in (self.root/'calls').read_text().splitlines()]
        create=next(call for call in calls if call[:2]==['release','create'])
        self.assertEqual(create[-3:],[str(self.inventory),*map(str,assets)])

    def test_a_new_release_carries_the_c_archives_after_the_github_assets(self):
        assets=self.github_assets()
        c=self.repo/'c'
        c.mkdir()
        for name in ('hyperuuid-0.10.0-win-x64.zip','hyperuuid-0.10.0-linux-x64.tar.gz'):
            (c/name).write_text(name)
        self.finalize()
        calls=[json.loads(line) for line in (self.root/'calls').read_text().splitlines()]
        create=next(call for call in calls if call[:2]==['release','create'])
        self.assertEqual(create[-5:],[str(self.inventory),*map(str,assets),*map(str,sorted(c.iterdir()))])

    def test_a_new_release_carries_the_php_zips_last_where_pie_downloads_them(self):
        assets=self.github_assets()
        c=self.repo/'c'
        c.mkdir()
        (c/'hyperuuid-0.10.0-linux-x64.tar.gz').write_text('c')
        php=self.repo/'php'
        php.mkdir()
        for name in ('php_hypercast-v0.10.0_php8.5-arm64-darwin-bsdlibc-nts.zip','php_hypercast-v0.10.0-8.3-nts-vs16-x86_64.zip'):
            (php/name).write_text(name)
        self.finalize()
        calls=[json.loads(line) for line in (self.root/'calls').read_text().splitlines()]
        create=next(call for call in calls if call[:2]==['release','create'])
        self.assertEqual(create[-6:],[str(self.inventory),*map(str,assets),*map(str,sorted(c.iterdir())),*map(str,sorted(php.iterdir()))])

    def test_an_existing_release_gets_only_its_missing_assets_and_keeps_matching_ones(self):
        assets=self.github_assets()
        os.environ['RELEASE_EXISTS']='1'
        os.environ['RELEASE_ASSETS']=json.dumps(['inventory.json',assets[0].name])
        os.environ['REMOTE_ASSETS']=json.dumps({assets[0].name:assets[0].name})
        for key in ('RELEASE_EXISTS','RELEASE_ASSETS','REMOTE_ASSETS'):
            self.addCleanup(os.environ.pop,key,None)
        self.finalize()
        calls=[json.loads(line) for line in (self.root/'calls').read_text().splitlines()]
        uploads=[call for call in calls if call[:2]==['release','upload']]
        self.assertEqual(uploads,[['release','upload','v0.10.0',str(assets[1]),'--repo','SkunkWerkx/HyperCast']])
        os.environ['REMOTE_ASSETS']=json.dumps({assets[0].name:'different bytes'})
        with self.assertRaisesRegex(ValueError,assets[0].name):
            self.finalize()

    def test_rejects_conflicting_existing_release_inventory(self):
        os.environ['RELEASE_EXISTS']='1'
        os.environ['REMOTE_INVENTORY']='different bytes'
        self.addCleanup(os.environ.pop,'RELEASE_EXISTS',None)
        self.addCleanup(os.environ.pop,'REMOTE_INVENTORY',None)
        with self.assertRaisesRegex(ValueError,'inventory'):
            self.finalize()


if __name__=='__main__': unittest.main()
