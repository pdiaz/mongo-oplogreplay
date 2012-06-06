import unittest2 as unittest

import pymongo
import time
import random
import threading

from oplogreplay import OplogReplayer

SOURCE_HOST = '127.0.0.1:27017'
SOURCE_REPLICASET = 'testsource'
DEST_HOST = '127.0.0.1:27018'

# Inherit from OplogReplayer to count number of processed_op methodcalls.
class CountingOplogReplayer(OplogReplayer):

    count = 0

    def process_op(self, ns, op, id, raw):
        OplogReplayer.process_op(self, ns, op, id, raw)
        CountingOplogReplayer.count += 1

class TestOplogReplayer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Create connections to both test databases.
        cls.source = pymongo.Connection(SOURCE_HOST)
        cls.dest = pymongo.Connection(DEST_HOST)

    def _start_replay(self):
        # Init & start OplogReplayer, in a separate thread.
        self.oplogreplay = CountingOplogReplayer(SOURCE_HOST, SOURCE_REPLICASET,
                                               DEST_HOST, poll_time=0.1)
        self.thread = threading.Thread(target=self.oplogreplay.start)
        self.thread.start()

    def _stop_replay(self):
        # Stop OplogReplayer & join its thread.
        self.oplogreplay.stop()
        self.thread.join()

    def setUp(self):
        # Drop test databases.
        self.source.drop_database('testdb')
        self.dest.drop_database('testdb')
        self.dest.drop_database('oplogreplay')
        # Sleep a little to allow drop database operations to complete.
        time.sleep(0.5)

        # Remember Database objects.
        self.sourcedb = self.source.testdb
        self.destdb = self.dest.testdb

        # Reset global counter.
        CountingOplogReplayer.count = 0
        # Remember number of oplogs before starting this test.
        self.oplog_count_before_test = self.source.local.oplog.rs.count()

        self._start_replay()

    def tearDown(self):
        self._stop_replay()

    def _synchronous_wait(self, target, timeout=3.0):
        """ Synchronously wait for the oplogreplay to finish.

        Waits until the oplog's retry_count hits target, but at most
        timeout seconds.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            if CountingOplogReplayer.count == target:
                return True
            time.sleep(0.1)
        return False

    def assertCollectionEqual(self, coll1, coll2):
        self.assertEqual(coll1.count(), coll2.count(),
                         msg='Collections have different count.')
        for obj1 in coll1.find():
            obj2 = coll2.find_one(obj1)
            self.assertEqual(obj1, obj2)

    def assertDatabaseEqual(self, db1, db2):
        self.assertListEqual(db1.collection_names(), db2.collection_names(),
                             msg='Databases have different collections.')
        for coll in db1.collection_names():
            self.assertCollectionEqual(db1[coll], db2[coll])

    def test_writes(self):
        self.sourcedb.testcoll.insert({'content': 'mycontent', 'nr': 1})
        self.sourcedb.testcoll.insert({'content': 'mycontent', 'nr': 2})
        self.sourcedb.testcoll.insert({'content': 'mycontent', 'nr': 3})
        self.sourcedb.testcoll.remove({'nr': 3})
        self.sourcedb.testcoll.insert({'content': 'mycontent', 'nr': 4})

        self.sourcedb.testcoll.insert({'content': 'mycontent', 'nr': 5})
        self.sourcedb.testcoll.insert({'content': '...', 'nr': 6})
        self.sourcedb.testcoll.update({'nr': 6}, {'$set': {'content': 'newContent'}})
        self.sourcedb.testcoll.update({'nr': 97}, {'$set': {'content': 'newContent'}})
        self.sourcedb.testcoll.update({'nr': 8}, {'$set': {'content': 'newContent'}}, upsert=True)

        self.sourcedb.testcoll.remove({'nr': 99})
        self.sourcedb.testcoll.remove({'nr': 3})
        self.sourcedb.testcoll.remove({'nr': 4})
        self.sourcedb.testcoll.insert({'content': 'new content', 'nr': 3})
        self.sourcedb.testcoll.insert({'content': 'new content', 'nr': 4})

        # Removes and updates that don't do anything will not hit the oplog:
        self._synchronous_wait(12)

        # Test that the 2 test databases are identical.
        self.assertDatabaseEqual(self.sourcedb, self.destdb)

    def _perform_bulk_inserts(self, nr=100):
        for i in xrange(nr):
            obj = { 'content': '%s' % random.random(),
                    'nr': random.randrange(100000) }
            self.sourcedb.testcoll.insert(obj)

    def test_bulk_inserts(self):
        self._perform_bulk_inserts(1000)

        self._synchronous_wait(1000)

        # Test that the 2 test databases are identical.
        self.assertDatabaseEqual(self.sourcedb, self.destdb)

    def test_discontinued_replay(self):
        self._perform_bulk_inserts(200)
        self._stop_replay()
        self._perform_bulk_inserts(150)
        self._start_replay()
        self._perform_bulk_inserts(100)

        self._synchronous_wait(450)

        # Test that the 2 test databases are identical.
        self.assertDatabaseEqual(self.sourcedb, self.destdb)

        # Test that no operation was replayed twice.
        self.assertEqual(CountingOplogReplayer.count, 450)