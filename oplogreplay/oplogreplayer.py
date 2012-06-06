import time
import pymongo
import logging

from oplogwatcher import OplogWatcher

class OplogReplayer(OplogWatcher):
    """ Replayes all oplogs from one mongo connection to another.

    Watches a mongo connection for write ops (source), and replays them
    into another mongo connection (destination).
    """

    def __init__(self, source, replicaset, dest, replay_indexes=True,
                 poll_time=1.0):
        # Mongo source is a replica set, connect to it as such.
        self.source = pymongo.Connection(source, replicaset=replicaset)
        # Use ReadPreference.SECONDARY because we can afford to read oplogs
        # from secondaries: even if they're behind, everything will work
        # correctly because the oplog order will always be preserved.
        self.source.read_preference = pymongo.ReadPreference.SECONDARY

        self._lastts_id = '%s-lastts' % replicaset
        self.dest = pymongo.Connection(dest)

        ts = self._get_lastts()
        self._replay_count = 0
        OplogWatcher.__init__(self, self.source, ts=ts, poll_time=poll_time)

    def print_replication_info(self):
        delay = int(time.time()) - self.ts.time
        logging.debug('synced = %ssecs ago (%.2fhrs)' % (delay, delay/3600.0))
        logging.debug('[%s] replayed %s ops' % (time.strftime('%x %X'),
                      self._replay_count))

    def _get_lastts(self):
        # Get the last oplog ts that was played on destination.
        obj = self.dest.oplogreplay.settings.find_one({'_id': self._lastts_id})
        if obj is None:
            return None
        else:
            return obj['value']

    def _update_lastts(self):
        self.dest.oplogreplay.settings.update({'_id': self._lastts_id},
                                              {'$set': {'value': self.ts}},
                                              upsert=True)

    def process_op(self, ns, op, id, raw):
        OplogWatcher.process_op(self, ns, op, id, raw)
        # Update the lastts on the destination
        self._update_lastts()
        self._replay_count += 1
        if self._replay_count % 100 == 0:
            self.print_replication_info()

    def _dest_coll(self, ns):
        db, collection = ns.split('.', 1)
        return self.dest[db][collection]

    def insert(self, ns, id, obj, raw, **kw):
        """ Perform a single insert operation.

            {'id': ObjectId('4e95ae77a20e6164850761cd'),
             'ns': u'mydb.tweets',
             'op': u'i',
             'raw': {u'h': -1469300750073380169L,
                     u'ns': u'mydb.tweets',
                     u'o': {u'_id': ObjectId('4e95ae77a20e6164850761cd'),
                            u'content': u'Lorem ipsum',
                            u'nr': 16},
                     u'op': u'i',
                     u'ts': Timestamp(1318432375, 1)},
             'ts': Timestamp(1318432375, 1)}
        """
        self._dest_coll(ns).insert(raw['o'], safe=True)

    def update(self, ns, id, mod, raw, **kw):
        """ Perform a single update operation.

            {'id': ObjectId('4e95ae3616692111bb000001'),
             'ns': u'mydb.tweets',
             'op': u'u',
             'raw': {u'h': -5295451122737468990L,
                     u'ns': u'mydb.tweets',
                     u'o': {u'$set': {u'content': u'Lorem ipsum'}},
                     u'o2': {u'_id': ObjectId('4e95ae3616692111bb000001')},
                     u'op': u'u',
                     u'ts': Timestamp(1318432339, 1)},
             'ts': Timestamp(1318432339, 1)}
        """
        self._dest_coll(ns).update(raw['o2'], raw['o'], safe=True)

    def delete(self, ns, id, raw, **kw):
        """ Perform a single delete operation.

            {'id': ObjectId('4e959ea11669210edc002902'),
             'ns': u'mydb.tweets',
             'op': u'd',
             'raw': {u'b': True,
                     u'h': -8347418295715732480L,
                     u'ns': u'mydb.tweets',
                     u'o': {u'_id': ObjectId('4e959ea11669210edc002902')},
                     u'op': u'd',
                     u'ts': Timestamp(1318432261, 10499)},
             'ts': Timestamp(1318432261, 10499)}
        """
        self._dest_coll(ns).remove(raw['o'], safe=True)