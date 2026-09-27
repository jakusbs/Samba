"""Owner-aware client regressions without a TANGO server."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from core import setup_lock as lock


class LegacyProxy:
    def __init__(self):
        self.owner = ''; self.busy = False; self.writes = []; self.compete = False
    def command_query(self, name):
        raise AttributeError(name)
    def read_attribute(self, name):
        return SimpleNamespace(value=self.busy if name.endswith('busy') else self.owner)
    def write_attribute(self, name, value):
        self.writes.append((name, value))
        if name.endswith('busy'):
            self.busy = value
            if self.compete:
                self.owner = 'competing-owner'
            elif not value:
                self.owner = ''
        else:
            self.owner = value


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.old_held, self.old_health = lock._held, lock._health
        lock._held, lock._health = {}, {}
    def tearDown(self):
        for held in lock._held.values():
            if held.get('stop') is not None:
                held['stop'].set()
        lock._held, lock._health = self.old_held, self.old_health

    def test_losing_legacy_client_never_clears_winner(self):
        proxy = LegacyProxy(); proxy.compete = True
        with patch.object(lock, '_get_proxy', return_value=proxy):
            ok, owner = lock.acquire_lock('Green')
            self.assertFalse(ok); self.assertEqual(owner, 'competing-owner')
            lock.release_lock('Green')
        self.assertTrue(proxy.busy)
        self.assertNotIn(('greenbusy', False), proxy.writes)
        self.assertEqual(proxy.owner, 'competing-owner')

    def test_legacy_release_checks_ownership_and_busy_is_not_stolen(self):
        proxy = LegacyProxy()
        with patch.object(lock, '_get_proxy', return_value=proxy):
            self.assertTrue(lock.acquire_lock('Green')[0])
            proxy.owner = 'new-owner'
            lock.release_lock('Green')
            self.assertTrue(proxy.busy)
            self.assertFalse(lock.acquire_lock('Green')[0])
        self.assertEqual(proxy.owner, 'new-owner')

    def test_atomic_client_uses_same_token_for_release(self):
        calls = []
        class AtomicProxy:
            def command_query(self, _):
                return True
            def command_inout(self, command, raw):
                payload = json.loads(raw); calls.append((command, payload))
                return json.dumps({'acquired': True}) if command == 'AcquireLease' else True
        with patch.object(lock, '_get_proxy', return_value=AtomicProxy()):
            self.assertTrue(lock.acquire_lock('IR')[0])
            self.assertEqual(lock.lock_status('IR'), 'Lease protected')
            lock.release_lock('IR')
        self.assertEqual([c[0] for c in calls], ['AcquireLease', 'ReleaseLease'])
        self.assertEqual(calls[0][1]['owner'], calls[1][1]['owner'])

    def test_failed_renewal_marks_ownership_unhealthy(self):
        class OneIteration:
            count = 0
            def wait(self, _):
                self.count += 1
                return self.count > 1
        lock._held['Green'] = {'token': 'owner'}
        with patch.object(lock, '_get_proxy', return_value=None):
            lock._renew_loop('Green', 'owner', OneIteration())
        self.assertFalse(lock.lock_health('Green')[0])

    def test_missing_optional_service_is_visibly_unprotected(self):
        with patch.object(lock, '_get_proxy', return_value=None):
            self.assertTrue(lock.acquire_lock('Cryo')[0])
        self.assertIn('Unprotected', lock.lock_status('Cryo'))
