"""Firestore klientining minimal soxta (in-memory) implementatsiyasi — test uchun."""
import itertools
import threading


class _Doc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
    @property
    def exists(self): return self._data is not None
    def to_dict(self): return dict(self._data) if self._data else None

class _DocRef:
    def __init__(self, store, coll, doc_id):
        self._store = store
        self._coll = coll
        self.id = doc_id
    def get(self, transaction=None):
        self._store.calls.append(("get", self._coll, self.id))
        self._store.block()
        return _Doc(self.id, self._store.data[self._coll].get(self.id))
    def set(self, payload, merge=False):
        self._store.calls.append(("set", self._coll, self.id))
        self._store.block()
        cur = self._store.data[self._coll].get(self.id, {}) if merge else {}
        cur.update(payload)
        self._store.data[self._coll][self.id] = cur
    def update(self, payload):
        self._store.calls.append(("update", self._coll, self.id))
        self._store.block()
        if self.id not in self._store.data[self._coll]:
            raise KeyError("NotFound")
        self._store.data[self._coll][self.id].update(payload)
    def collection(self, name):
        # Subkolleksiya (`shops/{id}/couriers`) — to'liq yo'l kalit sifatida
        # ishlatiladi, ya'ni test seed'ida "shops/s1/couriers" deb yoziladi.
        return _Query(self._store, f"{self._coll}/{self.id}/{name}")

class _Query:
    def __init__(self, store, coll, preds=None, limit=None, order=None, desc=False):
        self._store = store
        self._coll = coll
        self._preds = preds or []
        self._limit = limit
        self._order = order
        self._desc = desc
    def _clone(self, **kw):
        base = {"preds": self._preds, "limit": self._limit, "order": self._order, "desc": self._desc}
        base.update(kw)
        return _Query(self._store, self._coll, **base)
    def where(self, field=None, op=None, value=None, filter=None):
        if filter is not None:
            field, op, value = filter.field_path, filter.op_string, filter.value
        return self._clone(preds=[*self._preds, (field, op, value)])
    def limit(self, n): return self._clone(limit=n)
    def order_by(self, field, direction=None):
        return self._clone(order=field, desc=(direction == "DESCENDING"))
    def select(self, fields): return self
    def document(self, doc_id=None):
        return _DocRef(self._store, self._coll, doc_id or next(self._store.ids))
    def stream(self):
        self._store.calls.append(("stream", self._coll, None))
        self._store.block()
        rows = [(k, v) for k, v in self._store.data[self._coll].items()]
        for field, op, value in self._preds:
            assert op == "==", f"faqat '==' qo'llab-quvvatlanadi, keldi: {op}"
            rows = [(k, v) for k, v in rows if v.get(field) == value]
        if self._order:
            rows.sort(key=lambda kv: str(kv[1].get(self._order) or ""), reverse=self._desc)
        if self._limit:
            rows = rows[: self._limit]
        return [_Doc(k, v) for k, v in rows]

class _Transaction:
    """Firestore tranzaksiyasining soxta o'rnini bosuvchi.

    Haqiqiy tranzaksiya kabi izolyatsiya bermaydi — testlar ketma-ket
    chaqiruvlarni tekshiradi (birinchi kuryer yutadi, ikkinchisi
    "kechikdingiz" oladi), parallel ijroni emas. `is_fake` bayrog'i
    database/db.py ga haqiqiy `@transactional` bezovchisini o'tkazib
    yuborishni bildiradi.
    """
    is_fake = True

    def __init__(self, store):
        self._store = store

    def get(self, doc_ref):
        return doc_ref.get()

    def set(self, doc_ref, payload, merge=False):
        doc_ref.set(payload, merge=merge)

    def update(self, doc_ref, payload):
        doc_ref.update(payload)


class FakeFirestore:
    """`collection()` chaqiruvlarini qo'llab-quvvatlaydi; har bir I/O `block_ms` kutadi."""
    def __init__(self, data=None, block_ms=0.0):
        from collections import defaultdict
        self.data = defaultdict(dict)
        for k, v in (data or {}).items():
            self.data[k] = dict(v)
        self.ids = (f"auto{i}" for i in itertools.count(1))
        self.calls = []
        self.block_ms = block_ms
    def block(self):
        if self.block_ms:
            threading.Event().wait(self.block_ms)   # bloklovchi (async emas) kutish
    def collection(self, name): return _Query(self, name)
    def transaction(self): return _Transaction(self)
