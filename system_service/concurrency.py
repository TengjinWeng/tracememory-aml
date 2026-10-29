"""Concurrent operations with an exclusive, non-starving maintenance window."""
from contextlib import contextmanager
import threading
import weakref


class ActivityGate:
    def __init__(self):
        self.condition=threading.Condition()
        self.readers=0
        self.writer=False
        self.waiting=0
        self.scope_lock=threading.Lock()
        self.scopes=weakref.WeakValueDictionary()

    def user_lock(self,user):
        with self.scope_lock:
            lock=self.scopes.get(user)
            if lock is None:
                lock=threading.RLock();self.scopes[user]=lock
            return lock

    @contextmanager
    def read(self):
        with self.condition:
            while self.writer or self.waiting:self.condition.wait()
            self.readers+=1
        try:yield
        finally:
            with self.condition:
                self.readers-=1;self.condition.notify_all()

    @contextmanager
    def write(self):
        with self.condition:
            self.waiting+=1
            try:
                while self.writer or self.readers:self.condition.wait()
                self.writer=True
            finally:self.waiting-=1
        try:yield
        finally:
            with self.condition:
                self.writer=False;self.condition.notify_all()
