from contextlib import closing
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from clinic_ai import ai_models
from scripts.prepare_shortcuts import connect,workflow

class ShortcutSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'Shortcuts.sqlite';self.config=Path(self.tmp.name)/'binding.json'
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript('CREATE TABLE ZSHORTCUT (ZNAME TEXT,ZWORKFLOWID TEXT,ZACTIONS INT,ZTOMBSTONED INT); CREATE TABLE ZSHORTCUTACTIONS (Z_PK INT,ZDATA BLOB);')
            for i,model in enumerate(('Apple Intelligence','Apple Intelligence Pro'),1):
                name=ai_models.MODELS['cloud' if i==1 else 'cloud-pro'][2]
                db.execute('INSERT INTO ZSHORTCUT VALUES (?,?,?,0)',(name,f'12345678-1234-1234-1234-123456789ab{i}',i))
                db.execute('INSERT INTO ZSHORTCUTACTIONS VALUES (?,?)',(i,plistlib.dumps(workflow(model)['WFWorkflowActions'])))
            db.commit()
        self.patches=[patch.object(ai_models,'CONFIG',self.config),patch.object(ai_models,'SHORTCUTS_DB',self.db)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in self.patches:p.stop()
        self.tmp.cleanup()
    def test_setup_validates_models_hashes_and_export_types_without_writing_shortcuts(self):
        before=self.db.read_bytes();connect()
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(ai_models.resolve('cloud')['output_type'],'public.json')
        self.assertEqual(ai_models.resolve('cloud-pro')['output_type'],'public.plain-text')
        self.assertEqual(self.config.stat().st_mode & 0o777,0o600)
        for m in ('cloud','cloud-pro'):
            p=ai_models.resolve(m);self.assertEqual(ai_models.workflow_digest(p['shortcut_id']),p['workflow_sha256'])
    def test_wrong_model_or_fixed_prompt_is_not_bound(self):
        with closing(sqlite3.connect(self.db)) as db:
            actions=workflow('Apple Intelligence Pro')['WFWorkflowActions']
            actions[1]['WFWorkflowActionParameters']['WFLLMPrompt']='1+1은?'
            db.execute('UPDATE ZSHORTCUTACTIONS SET ZDATA=? WHERE Z_PK=1',(plistlib.dumps(actions),));db.commit()
        with self.assertRaises(SystemExit):connect()
        self.assertFalse(self.config.exists())
