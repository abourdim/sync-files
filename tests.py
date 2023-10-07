#!/usr/bin/env python3
"""
SyncFiles — Deep Test Suite
Run: python3 tests.py
     python3 tests.py -v
     python3 tests.py TestStress
"""
import sys,os,json,time,stat,shutil,tempfile,threading,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))

class TempEnv:
    def __init__(self):
        self.td=tempfile.mkdtemp();self.src=os.path.join(self.td,'source');self.dst=os.path.join(self.td,'dest')
        os.makedirs(self.src);os.makedirs(self.dst)
        from server.config import Config
        self.config=Config(os.path.join(self.td,'config.yaml'));self.config.set('sync','source',self.src);self.config.set('sync','chunk_size',1024)
        self.dest={'type':'local','path':self.dst,'name':'test'}
    def mkfile(self,name,content='hello',base=None):
        base=base or self.src;p=os.path.join(base,name);os.makedirs(os.path.dirname(p),exist_ok=True)
        m='wb' if isinstance(content,bytes) else 'w'
        with open(p,m) as f:f.write(content)
        return p
    def cleanup(self):
        for r,ds,fs in os.walk(self.td):
            for d in ds:
                try:os.chmod(os.path.join(r,d),0o755)
                except:pass
            for f in fs:
                try:os.chmod(os.path.join(r,f),0o644)
                except:pass
        shutil.rmtree(self.td,ignore_errors=True)

# ═══════ CONFIG ═══════
class TestConfig(unittest.TestCase):
    def setUp(self):self.td=tempfile.mkdtemp();self.p=os.path.join(self.td,'c.yaml')
    def tearDown(self):shutil.rmtree(self.td,ignore_errors=True)
    def _c(self):
        from server.config import Config;c=Config(self.p);c.create_default();return c
    def test_defaults(self):c=self._c();self.assertEqual(c.get('server','port'),8765);self.assertEqual(c.get('sync','chunk_size'),4194304)
    def test_set_get(self):c=self._c();c.set('server','port',9999);self.assertEqual(c.get('server','port'),9999)
    def test_section(self):self.assertIsInstance(self._c().get('server'),dict)
    def test_to_dict(self):self.assertIn('sync',self._c().to_dict())
    def test_validate_ok(self):self.assertEqual(self._c().validate(),[])
    def test_validate_bad_port(self):c=self._c();c.set('server','port',-1);self.assertTrue(any('port'in i for i in c.validate()))
    def test_validate_bad_interval(self):c=self._c();c.set('sync','interval',0);self.assertTrue(any('interval'in i for i in c.validate()))
    def test_validate_bad_chunk(self):c=self._c();c.set('sync','chunk_size',10);self.assertTrue(any('chunk'in i for i in c.validate()))
    def test_persistence(self):
        c=self._c();c.set('server','port',1234)
        from server.config import Config;self.assertEqual(Config(self.p).get('server','port'),1234)
    def test_env_override(self):
        os.environ['SYNCFILES_PORT']='4444'
        try:
            from server.config import Config;c=Config(self.p);c.create_default();self.assertEqual(c.get('server','port'),4444)
        finally:del os.environ['SYNCFILES_PORT']
    def test_missing_file(self):
        from server.config import Config;self.assertEqual(Config(os.path.join(self.td,'nope.yaml')).get('server','port'),8765)
    def test_corrupt_file(self):
        with open(self.p,'w') as f:f.write('{{{bad')
        from server.config import Config;self.assertEqual(Config(self.p).get('server','port'),8765)

# ═══════ CREDENTIALS ═══════
class TestCredentials(unittest.TestCase):
    def setUp(self):self.td=tempfile.mkdtemp()
    def tearDown(self):shutil.rmtree(self.td,ignore_errors=True)
    def _cs(self,pw='p'):
        from server.credentials import CredentialStore;cs=CredentialStore(self.td);cs.initialize(pw);return cs
    def test_lifecycle(self):cs=self._cs();cs.set('s','k','v');self.assertEqual(cs.get('s','k'),'v');cs.delete('s','k');self.assertFalse(cs.has('s','k'))
    def test_lock_unlock(self):cs=self._cs('s');cs.set('a','b','c');
    def test_lock_unlock_2(self):
        cs=self._cs('s');cs.set('a','b','c')
        from server.credentials import CredentialStore;cs2=CredentialStore(self.td);self.assertTrue(cs2.unlock('s'));self.assertEqual(cs2.get('a','b'),'c')
    def test_wrong_pwd(self):
        self._cs('right')
        from server.credentials import CredentialStore;self.assertFalse(CredentialStore(self.td).unlock('wrong'))
    def test_change_master(self):
        cs=self._cs('old');cs.set('x','y','z');cs.change_master('old','new')
        from server.credentials import CredentialStore;cs2=CredentialStore(self.td);self.assertTrue(cs2.unlock('new'));self.assertEqual(cs2.get('x','y'),'z')
    def test_locked_raises(self):
        self._cs()
        from server.credentials import CredentialStore;cs2=CredentialStore(self.td)
        with self.assertRaises(RuntimeError):cs2.get('t')
    def test_delete_service(self):cs=self._cs();cs.set('s','a','1');cs.delete('s');self.assertFalse(cs.has('s'))
    def test_list(self):cs=self._cs();cs.set('a','k','v');cs.set('b','k','v');s=cs.list_services();self.assertIn('a',s)
    def test_nonexistent(self):cs=self._cs();self.assertIsNone(cs.get('nope','nope'))
    def test_unicode(self):cs=self._cs();cs.set('s','k','مرحبا 🔑');self.assertEqual(cs.get('s','k'),'مرحبا 🔑')
    def test_large(self):cs=self._cs();big='x'*100000;cs.set('s','k',big);self.assertEqual(cs.get('s','k'),big)
    def test_permissions(self):self._cs();self.assertEqual(oct(os.stat(os.path.join(self.td,'.credentials')).st_mode)[-3:],'700')

# ═══════ CHUNK HASH ═══════
class TestChunkHash(unittest.TestCase):
    def setUp(self):self.td=tempfile.mkdtemp()
    def tearDown(self):shutil.rmtree(self.td,ignore_errors=True)
    def _f(self,n,c):
        p=os.path.join(self.td,n);m='wb'if isinstance(c,bytes)else'w'
        with open(p,m)as f:f.write(c)
        return p
    def test_hash_det(self):
        from server.chunk_hash import compute_file_hash as h;f=self._f('t','hi');self.assertEqual(h(f),h(f))
    def test_hash_diff(self):
        from server.chunk_hash import compute_file_hash as h;self.assertNotEqual(h(self._f('a','a')),h(self._f('b','b')))
    def test_manifest(self):
        from server.chunk_hash import compute_chunk_manifest as cm;m=cm(self._f('t','x'*5000),1024);self.assertEqual(len(m['chunks']),5)
    def test_delta_identical(self):
        from server.chunk_hash import compute_chunk_manifest as cm,compute_delta as cd;m=cm(self._f('t','x'*3000),1024);self.assertEqual(cd(m,m),[])
    def test_delta_one_chunk(self):
        from server.chunk_hash import compute_chunk_manifest as cm,compute_delta as cd
        m1=cm(self._f('a','A'*1024+'B'*1024),1024);m2=cm(self._f('b','A'*1024+'C'*1024),1024);self.assertEqual(cd(m1,m2),[1])
    def test_roundtrip(self):
        from server.chunk_hash import compute_chunk_manifest as cm,extract_chunks,apply_chunks,compute_file_hash as h
        f1=self._f('s','hello '*500);m=cm(f1,1024);ch=extract_chunks(f1,[c['index']for c in m['chunks']],1024)
        f2=self._f('d','\x00'*m['total_size']);apply_chunks(f2,ch,m);self.assertEqual(h(f1),h(f2))
    def test_cache(self):
        from server.chunk_hash import compute_chunk_manifest as cm,ChunkCache
        c=ChunkCache(os.path.join(self.td,'c.json'));f=self._f('t','d');m=cm(f,1024);c.put(f,m);self.assertIsNotNone(c.get(f))
        with open(f,'w')as fh:fh.write('x');self.assertIsNone(c.get(f))
    def test_empty(self):
        from server.chunk_hash import compute_file_hash as h,compute_chunk_manifest as cm
        f=self._f('e','');self.assertEqual(len(h(f)),64);m=cm(f,1024);self.assertEqual(len(m['chunks']),0)
    def test_binary(self):
        from server.chunk_hash import compute_chunk_manifest as cm;m=cm(self._f('b',bytes(range(256))*10),1024);self.assertGreater(len(m['chunks']),0)
    def test_exact_chunk(self):
        from server.chunk_hash import compute_chunk_manifest as cm;m=cm(self._f('e','x'*1024),1024);self.assertEqual(len(m['chunks']),1)

# ═══════ CONFLICT ═══════
class TestConflict(unittest.TestCase):
    def setUp(self):self.td=tempfile.mkdtemp()
    def tearDown(self):shutil.rmtree(self.td,ignore_errors=True)
    def _cd(self):
        from server.conflict import ConflictDetector;return ConflictDetector(self.td)
    def _i(self,h='a',m=1,s=1):return{'hash':h,'mtime':m,'size':s}
    def test_both_changed(self):self.assertEqual(self._cd().check('f',self._i('a'),self._i('b'),self._i('c')),'conflict')
    def test_both_changed_same(self):self.assertEqual(self._cd().check('f',self._i('x'),self._i('x'),self._i('c')),'skip')
    def test_local_changed_remote_del(self):self.assertEqual(self._cd().check('f',self._i('a'),None,self._i('c')),'conflict')
    def test_local_unchanged_remote_del(self):self.assertEqual(self._cd().check('f',self._i('c'),None,self._i('c')),'delete_local')
    def test_local_del_remote_unchanged(self):self.assertEqual(self._cd().check('f',None,self._i('c'),self._i('c')),'delete_remote')
    def test_local_del_remote_changed(self):self.assertEqual(self._cd().check('f',None,self._i('x'),self._i('c')),'conflict')
    def test_only_local(self):self.assertEqual(self._cd().check('f',self._i('a'),self._i('c'),self._i('c')),'upload')
    def test_only_remote(self):self.assertEqual(self._cd().check('f',self._i('c'),self._i('b'),self._i('c')),'download')
    def test_nothing(self):self.assertEqual(self._cd().check('f',self._i('c'),self._i('c'),self._i('c')),'skip')
    def test_new_local(self):self.assertEqual(self._cd().check('f',self._i(),None,None),'upload')
    def test_new_remote(self):self.assertEqual(self._cd().check('f',None,self._i(),None),'download')
    def test_new_both_same(self):self.assertEqual(self._cd().check('f',self._i('a'),self._i('a'),None),'skip')
    def test_new_both_diff(self):self.assertEqual(self._cd().check('f',self._i('a'),self._i('b'),None),'conflict')
    def test_both_none(self):self.assertEqual(self._cd().check('f',None,None,None),'skip')
    def test_both_none_last(self):self.assertEqual(self._cd().check('f',None,None,self._i()),'skip')
    def test_register_resolve(self):
        cd=self._cd()
        with open(os.path.join(self.td,'f.txt'),'w')as f:f.write('x')
        cid=cd.register_conflict('f.txt',self._i(),self._i('b'),'d');self.assertEqual(cd.count(),1)
        cd.resolve(cid,'keep_local');self.assertEqual(cd.count(),0);self.assertEqual(len(cd.list_history()),1)
    def test_backup(self):
        cd=self._cd()
        with open(os.path.join(self.td,'f.txt'),'w')as f:f.write('orig')
        cid=cd.register_conflict('f.txt',self._i(),self._i('b'),'d');c=cd.get(cid)
        self.assertTrue(os.path.exists(os.path.join(self.td,c['backup_path'])))
    def test_persistence(self):
        cd=self._cd()
        with open(os.path.join(self.td,'f.txt'),'w')as f:f.write('x')
        cd.register_conflict('f.txt',self._i(),self._i('b'),'d')
        from server.conflict import ConflictDetector;self.assertEqual(ConflictDetector(self.td).count(),1)
    def test_file_info(self):
        from server.conflict import get_file_info
        f=os.path.join(self.td,'x');
        with open(f,'w')as fh:fh.write('test')
        i=get_file_info(f);self.assertEqual(i['size'],4);self.assertIsNone(get_file_info('/nope'))

# ═══════ WATCHER ═══════
class TestWatcher(unittest.TestCase):
    def setUp(self):self.td=tempfile.mkdtemp()
    def tearDown(self):shutil.rmtree(self.td,ignore_errors=True)
    def test_ignore_defaults(self):
        from server.watcher import SyncIgnore;si=SyncIgnore(self.td)
        for p in['.venv/x','__pycache__/x','a.pyc','.DS_Store','.credentials/x']:self.assertTrue(si.should_ignore(p),p)
        for p in['app.py','README.md']:self.assertFalse(si.should_ignore(p),p)
    def test_ignore_custom(self):
        from server.watcher import SyncIgnore
        with open(os.path.join(self.td,'.syncignore'),'w')as f:f.write('*.log\nbuild/\n')
        si=SyncIgnore(self.td);self.assertTrue(si.should_ignore('app.log'));self.assertFalse(si.should_ignore('app.py'))
    def test_reload(self):
        from server.watcher import SyncIgnore;si=SyncIgnore(self.td)
        with open(os.path.join(self.td,'.syncignore'),'w')as f:f.write('*.tmp\n')
        si.reload();self.assertTrue(si.should_ignore('x.tmp'))
    def test_lifecycle(self):
        from server.watcher import FileWatcher;ev=[]
        fw=FileWatcher(self.td,lambda e:ev.extend(e),debounce=0.1);fw.start();self.assertTrue(fw.is_running())
        with open(os.path.join(self.td,'t.txt'),'w')as f:f.write('x')
        time.sleep(0.4);fw.stop();self.assertFalse(fw.is_running());self.assertGreaterEqual(len(ev),1)
    def test_ignores_patterns(self):
        from server.watcher import FileWatcher,SyncIgnore
        with open(os.path.join(self.td,'.syncignore'),'w')as f:f.write('*.tmp\n')
        ev=[];fw=FileWatcher(self.td,lambda e:ev.extend(e),sync_ignore=SyncIgnore(self.td),debounce=0.1)
        fw.start()
        with open(os.path.join(self.td,'a.tmp'),'w')as f:f.write('no')
        with open(os.path.join(self.td,'a.txt'),'w')as f:f.write('yes')
        time.sleep(0.4);fw.stop()
        paths=[e['rel_path']for e in ev];self.assertNotIn('a.tmp',paths);self.assertIn('a.txt',paths)

# ═══════ SYNC ENGINE ═══════
class TestSyncEngine(unittest.TestCase):
    def setUp(self):
        self.env=TempEnv();self.events=[]
        from server.sync_engine import SyncEngine
        self.engine=SyncEngine(self.env.config,event_callback=lambda t,d:self.events.append((t,d)))
    def tearDown(self):self.env.cleanup()
    def test_first_sync(self):
        self.env.mkfile('a.txt','a');self.env.mkfile('b.txt','b')
        s=self.engine.sync(self.env.dest);self.assertEqual(s['uploaded'],2);self.assertEqual(s['errors'],0)
    def test_resync_skip(self):
        self.env.mkfile('a.txt','a');self.engine.sync(self.env.dest)
        s=self.engine.sync(self.env.dest);self.assertEqual(s['uploaded'],0)
    def test_modified(self):
        self.env.mkfile('a.txt','old');self.engine.sync(self.env.dest);time.sleep(0.05)
        self.env.mkfile('a.txt','NEW');s=self.engine.sync(self.env.dest);self.assertGreaterEqual(s['uploaded'],1)
        with open(os.path.join(self.env.dst,'a.txt'))as f:self.assertIn('NEW',f.read())
    def test_remote_dl(self):
        self.env.mkfile('a.txt','a');self.engine.sync(self.env.dest)
        self.env.mkfile('r.txt','remote',base=self.env.dst);s=self.engine.sync(self.env.dest)
        self.assertGreaterEqual(s['downloaded'],1);self.assertTrue(os.path.exists(os.path.join(self.env.src,'r.txt')))
    def test_nested(self):
        self.env.mkfile('a/b/c/d.txt','deep');self.engine.sync(self.env.dest)
        self.assertTrue(os.path.exists(os.path.join(self.env.dst,'a/b/c/d.txt')))
    def test_status(self):
        self.env.mkfile('a.txt','a');self.engine.sync(self.env.dest)
        st=self.engine.get_status();self.assertGreater(st['files_tracked'],0)
    def test_tree(self):
        self.env.mkfile('a.txt','a');self.engine.sync(self.env.dest)
        self.assertIn('a.txt',self.engine.get_file_tree())
    def test_history(self):
        self.env.mkfile('a.txt','a');self.engine.sync(self.env.dest)
        self.assertGreater(len(self.engine.get_sync_history()),0)
    def test_progress(self):
        self.env.mkfile('a.txt','a');self.engine.sync(self.env.dest)
        self.assertGreater(len([e for e in self.events if e[0]=='sync:progress']),0)
    def test_complete(self):
        self.env.mkfile('a.txt','a');self.engine.sync(self.env.dest)
        c=[e for e in self.events if e[0]=='sync:complete'];self.assertEqual(len(c),1);self.assertIn('duration',c[0][1])
    def test_syncignore(self):
        with open(os.path.join(self.env.src,'.syncignore'),'w')as f:f.write('*.log\n')
        # Re-create engine so it picks up the new .syncignore
        from server.sync_engine import SyncEngine
        engine=SyncEngine(self.env.config)
        self.env.mkfile('a.log','log');self.env.mkfile('a.py','code');engine.sync(self.env.dest)
        self.assertFalse(os.path.exists(os.path.join(self.env.dst,'a.log')))
        self.assertTrue(os.path.exists(os.path.join(self.env.dst,'a.py')))

# ═══════ GIT ═══════
class TestGit(unittest.TestCase):
    def setUp(self):self.td=tempfile.mkdtemp()
    def tearDown(self):shutil.rmtree(self.td,ignore_errors=True)
    def _gs(self):
        from server.git_sync import GitSync;gs=GitSync(self.td);gs.init_repo();return gs
    def test_not_repo(self):
        from server.git_sync import GitSync;self.assertFalse(GitSync(self.td).is_repo())
    def test_init(self):self.assertTrue(self._gs().is_repo())
    def test_commit(self):
        gs=self._gs()
        with open(os.path.join(self.td,'t'),'w')as f:f.write('x')
        self.assertIn('sha',gs.commit('t'))
    def test_nothing_to_commit(self):
        gs=self._gs()
        with open(os.path.join(self.td,'t'),'w')as f:f.write('x')
        gs.commit('1');self.assertIsNone(gs.commit('2'))
    def test_clean(self):
        gs=self._gs()
        with open(os.path.join(self.td,'t'),'w')as f:f.write('x')
        gs.commit('i');self.assertFalse(gs.status()['is_dirty'])
    def test_dirty(self):
        gs=self._gs()
        with open(os.path.join(self.td,'t'),'w')as f:f.write('x')
        gs.commit('i')
        with open(os.path.join(self.td,'t'),'w')as f:f.write('y')
        self.assertTrue(gs.status()['is_dirty'])
    def test_log(self):
        gs=self._gs()
        with open(os.path.join(self.td,'t'),'w')as f:f.write('x')
        gs.commit('first')
        with open(os.path.join(self.td,'t'),'w')as f:f.write('y')
        gs.commit('second');self.assertEqual(len(gs.log(10)),2)
    def test_diff(self):
        gs=self._gs()
        with open(os.path.join(self.td,'t'),'w')as f:f.write('x')
        gs.commit('i')
        with open(os.path.join(self.td,'t'),'w')as f:f.write('y')
        self.assertGreater(len(gs.diff()),0)
    def test_no_remote(self):self.assertFalse(self._gs().test_connection()['ok'])

# ═══════ GDRIVE + SSH ═══════
class TestGDrive(unittest.TestCase):
    def test_no_config(self):
        from server.gdrive import GDriveSync;from server.credentials import CredentialStore
        td=tempfile.mkdtemp()
        try:cs=CredentialStore(td);cs.initialize('t');self.assertFalse(GDriveSync(cs).test_connection()['ok'])
        finally:shutil.rmtree(td,ignore_errors=True)
class TestSSH(unittest.TestCase):
    def test_no_conn(self):
        from server.ssh_sync import SSHSync;from server.credentials import CredentialStore
        td=tempfile.mkdtemp()
        try:cs=CredentialStore(td);cs.initialize('t');self.assertFalse(SSHSync(cs).test_connection()['ok'])
        finally:shutil.rmtree(td,ignore_errors=True)

# ═══════ APP ═══════
class TestApp(unittest.TestCase):
    def test_imports(self):from server.app import Config,SyncEngine,CredentialStore,GitSync,GDriveSync
    def test_auth(self):
        from server import app;app.cred_store=None;self.assertIsNotNone(app.handle_auth('x'))
    def test_status(self):
        from server import app;app.engine=None;app.auto_sync_task=None;app.watcher=None
        st=app.get_full_status()
        for k in['syncing','files_tracked','connections','watcher_running']:self.assertIn(k,st)

# ═══════ FRONTEND ═══════
class TestFrontend(unittest.TestCase):
    def test_refs(self):
        with open('client/index.html')as f:h=f.read()
        self.assertIn('style.css',h);self.assertIn('sync.css',h);self.assertIn('script.js',h)
    def test_ids(self):
        import re
        with open('client/index.html')as f:h=f.read()
        ids=set(re.findall(r'id="(\w+)"',h))
        for r in['btnSyncNow','autoSyncToggle','statFiles','statPending','statConflicts','statLastSync',
                  'fileTree','conflictList','gitBranch','loginOverlay','syncIntervalSlider','connGdrive',
                  'connGit','connSSH','destList','gitLogList']:
            self.assertIn(r,ids,f'missing:{r}')
    def test_i18n(self):
        import re
        with open('client/index.html')as f:h=f.read()
        with open('client/script.js')as f:j=f.read()
        keys=set(re.findall(r'data-i18n="(\w+)"',h));self.assertGreater(len(keys),30)
        for k in keys:self.assertIn(k,j,f'missing i18n:{k}')
    def test_manifest(self):self.assertIn('SyncFiles',json.load(open('client/manifest.json'))['name'])
    def test_bash(self):self.assertEqual(os.system('bash -n sync.sh'),0)

# ═══════ EDGE CASES ═══════
class TestEdge(unittest.TestCase):
    def setUp(self):
        self.env=TempEnv()
        from server.sync_engine import SyncEngine;self.engine=SyncEngine(self.env.config)
    def tearDown(self):self.env.cleanup()
    def test_empty_file(self):
        self.env.mkfile('e.txt','');s=self.engine.sync(self.env.dest)
        self.assertEqual(s['uploaded'],1);self.assertEqual(os.path.getsize(os.path.join(self.env.dst,'e.txt')),0)
    def test_binary(self):
        self.env.mkfile('b.dat',bytes(range(256))*10);s=self.engine.sync(self.env.dest)
        self.assertEqual(s['uploaded'],1)
        with open(os.path.join(self.env.dst,'b.dat'),'rb')as f:self.assertEqual(len(f.read()),2560)
    def test_unicode_filename(self):
        self.env.mkfile('مرحبا.txt','ar');self.env.mkfile('日本語.txt','jp')
        s=self.engine.sync(self.env.dest);self.assertEqual(s['uploaded'],2)
    def test_unicode_content(self):
        self.env.mkfile('u.txt','مرحبا 🌍 你好');self.engine.sync(self.env.dest)
        with open(os.path.join(self.env.dst,'u.txt'))as f:self.assertIn('🌍',f.read())
    def test_deep_nest(self):
        self.env.mkfile('a/b/c/d/e/f/g/h.txt','deep');self.engine.sync(self.env.dest)
        self.assertTrue(os.path.exists(os.path.join(self.env.dst,'a/b/c/d/e/f/g/h.txt')))
    def test_spaces(self):
        self.env.mkfile('my file (1).txt','sp');self.assertEqual(self.engine.sync(self.env.dest)['uploaded'],1)
    def test_dotfile(self):
        self.env.mkfile('.hidden','x');self.assertEqual(self.engine.sync(self.env.dest)['uploaded'],1)
    def test_large_delta(self):
        d=b'A'*5120+b'B'*5120;self.env.mkfile('big.bin',d);self.engine.sync(self.env.dest)
        d2=b'X'*5120+b'B'*5120;self.env.mkfile('big.bin',d2);s=self.engine.sync(self.env.dest)
        self.assertGreaterEqual(s['uploaded'],1)
        with open(os.path.join(self.env.dst,'big.bin'),'rb')as f:c=f.read()
        self.assertTrue(c.startswith(b'X'*100));self.assertTrue(c.endswith(b'B'*100))
    def test_symlink(self):
        r=self.env.mkfile('real.txt','d');l=os.path.join(self.env.src,'link.txt')
        try:os.symlink(r,l)
        except OSError:self.skipTest('no symlinks')
        s=self.engine.sync(self.env.dest);self.assertEqual(s['errors'],0)

# ═══════ ERROR HANDLING ═══════
class TestErrors(unittest.TestCase):
    def setUp(self):
        self.env=TempEnv()
        from server.sync_engine import SyncEngine;self.engine=SyncEngine(self.env.config)
    def tearDown(self):self.env.cleanup()
    def test_missing_dest_created(self):
        nd=os.path.join(self.env.td,'new');self.env.mkfile('a.txt','d')
        s=self.engine.sync({'type':'local','path':nd,'name':'n'});self.assertTrue(os.path.isdir(nd));self.assertEqual(s['uploaded'],1)
    def test_corrupt_state(self):
        sd=os.path.join(self.env.src,'.sync_state');os.makedirs(sd,exist_ok=True)
        with open(os.path.join(sd,'state.json'),'w')as f:f.write('{bad')
        from server.sync_engine import SyncEngine;e=SyncEngine(self.env.config);self.assertEqual(e.get_status()['files_tracked'],0)
    def test_corrupt_conflicts(self):
        sd=os.path.join(self.env.src,'.sync_state');os.makedirs(sd,exist_ok=True)
        with open(os.path.join(sd,'conflicts.json'),'w')as f:f.write('nope')
        from server.conflict import ConflictDetector;self.assertEqual(ConflictDetector(self.env.src).count(),0)
    def test_corrupt_history(self):
        sd=os.path.join(self.env.src,'.sync_state');os.makedirs(sd,exist_ok=True)
        with open(os.path.join(sd,'history.json'),'w')as f:f.write('bad')
        from server.sync_engine import SyncEngine;self.assertEqual(SyncEngine(self.env.config).get_sync_history(),[])
    def test_corrupt_cache(self):
        from server.chunk_hash import ChunkCache;cp=os.path.join(self.env.td,'c.json')
        with open(cp,'w')as f:f.write('x');self.assertIsNone(ChunkCache(cp).get('y'))
    def test_readonly_source(self):
        f=self.env.mkfile('ro.txt','d');os.chmod(f,0o444)
        s=self.engine.sync(self.env.dest);self.assertEqual(s['uploaded'],1)
    def test_unknown_dest_type(self):
        self.env.mkfile('a.txt','d');s=self.engine.sync({'type':'ftp','path':'x'})
        # Should not crash

# ═══════ CONCURRENCY ═══════
class TestConcurrency(unittest.TestCase):
    def setUp(self):self.env=TempEnv()
    def tearDown(self):self.env.cleanup()
    def test_double_sync_rejected(self):
        from server.sync_engine import SyncEngine;e=SyncEngine(self.env.config)
        e._syncing=True;r=e.sync(self.env.dest);self.assertIn('error',r);e._syncing=False
    def test_concurrent_writes(self):
        from server.sync_engine import SyncEngine;e=SyncEngine(self.env.config);errs=[]
        def w(n):
            try:
                for i in range(20):self.env.mkfile(f't{n}_{i}.txt',f'c{n}{i}');time.sleep(0.005)
            except Exception as ex:errs.append(ex)
        ts=[threading.Thread(target=w,args=(n,))for n in range(3)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(len(errs),0);s=e.sync(self.env.dest);self.assertEqual(s['errors'],0);self.assertEqual(s['uploaded'],60)
    def test_watcher_during_sync(self):
        from server.sync_engine import SyncEngine;from server.watcher import FileWatcher,SyncIgnore
        e=SyncEngine(self.env.config);self.env.mkfile('x.txt','d');e.sync(self.env.dest)
        ev=[];fw=FileWatcher(self.env.src,lambda x:ev.extend(x),sync_ignore=SyncIgnore(self.env.src),debounce=0.1)
        fw.start()
        for i in range(10):self.env.mkfile(f'n{i}.txt',f'd{i}');time.sleep(0.03)
        time.sleep(0.3);s=e.sync(self.env.dest);fw.stop();self.assertEqual(s['errors'],0)
    def test_cred_thread_safety(self):
        from server.credentials import CredentialStore;cs=CredentialStore(self.env.td);cs.initialize('p');cs.set('s','k','v');errs=[]
        def r():
            try:
                for _ in range(50):assert cs.get('s','k')=='v'
            except Exception as ex:errs.append(ex)
        ts=[threading.Thread(target=r)for _ in range(5)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(len(errs),0)

# ═══════ STRESS ═══════
class TestStress(unittest.TestCase):
    def setUp(self):
        self.env=TempEnv()
        from server.sync_engine import SyncEngine;self.engine=SyncEngine(self.env.config)
    def tearDown(self):self.env.cleanup()
    def test_500_files(self):
        for i in range(500):self.env.mkfile(f'f_{i:04d}.txt',f'c{i}')
        s=self.engine.sync(self.env.dest);self.assertEqual(s['uploaded'],500);self.assertEqual(s['errors'],0)
        s2=self.engine.sync(self.env.dest);self.assertEqual(s2['uploaded'],0);self.assertEqual(s2['skipped'],500)
    def test_deep_20(self):
        p='/'.join([f'd{i}'for i in range(20)])+'/f.txt';self.env.mkfile(p,'deep')
        self.engine.sync(self.env.dest);self.assertTrue(os.path.exists(os.path.join(self.env.dst,p)))
    def test_rapid_mods(self):
        for i in range(50):self.env.mkfile('r.txt',f'v{i}')
        s=self.engine.sync(self.env.dest);self.assertEqual(s['uploaded'],1)
        with open(os.path.join(self.env.dst,'r.txt'))as f:self.assertIn('v49',f.read())
    def test_mixed_sizes(self):
        self.env.mkfile('e.txt','');self.env.mkfile('s.txt','x');self.env.mkfile('m.txt','x'*10000);self.env.mkfile('l.bin',b'\x00'*100000)
        s=self.engine.sync(self.env.dest);self.assertEqual(s['uploaded'],4);self.assertEqual(s['errors'],0)
    def test_100_dirs(self):
        for i in range(100):self.env.mkfile(f'd{i:03d}/f.txt',f'c{i}')
        s=self.engine.sync(self.env.dest);self.assertEqual(s['uploaded'],100);self.assertEqual(s['errors'],0)
    def test_history_cap(self):
        for i in range(10):self.env.mkfile(f'f{i}.txt',f'v{i}');self.engine.sync(self.env.dest);self.env.mkfile(f'f{i}.txt',f'v{i}m')
        self.assertLessEqual(len(self.engine.get_sync_history()),200)

if __name__=='__main__':
    os.chdir(str(Path(__file__).parent));unittest.main(verbosity=2)
