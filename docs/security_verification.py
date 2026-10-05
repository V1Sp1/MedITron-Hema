"""Verify current research protections using generated synthetic data only.

Run: .venv/bin/python docs/security_verification.py
Uses temporary test storage; never opens a project's existing medical database.
Historical security_audit_evidence.json is not overwritten.
"""
import hashlib
import io
import json
import platform
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_security.py')
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=2).run(suite)
    paths = ['backend/app.py','backend/store.py','backend/privacy.py','backend/auth.py',
             'backend/model_service.py','backend/worker_limits.py','backend/parser_service.py',
             'frontend/src/privacy.js','frontend/src/api.js','frontend/src/app.js','backend/integrations.py','scripts/bundle_project.py','tests/test_security.py']
    evidence = {'createdAt':datetime.now(timezone.utc).isoformat(),
                'scope':'Synthetic research regression tests; no existing patient stores opened',
                'python':platform.python_version(),'platform':platform.system(),
                'testsRun':result.testsRun,'success':result.wasSuccessful(),
                'failedTestIds':[test.id() for test, _ in result.failures + result.errors],
                'skippedTestIds':[test.id() for test, _ in result.skipped],
                'sources':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths},
                'limitations':['Not legal certification or clinical validation','Not native Windows integration test',
                               'Historical on-disk stores and downloaded user copies are not purged']}
    (ROOT/'docs/security_remediation_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:evidence[k] for k in ['testsRun','success','failedTestIds']},ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
