"""Every storage backend implements the whole interface, checked here.

This exists because of a real failure rather than as tidiness. `aopen_range`
was added to the base class, to S3 and to the null backend, and missed on
MinIO. Nothing local caught it: `ENVIRONMENT=test` selects `NullFileSystem`,
which was implemented, so the entire suite passed. CI selects MinIO and died
at import with

    TypeError: Can't instantiate abstract class MinioFileSystem without an
    implementation for abstract method 'aopen_range'

which is the API not starting at all, found one push too late.

The same edit also moved `@abstractmethod` off `aread_bytes` and onto the new
method, so `aread_bytes` quietly stopped being part of the contract. That is
the worse half: a missing `aopen_range` fails loudly at import, while a
backend silently lacking `aread_bytes` would fail at the moment a customer
asked for a transcript.

So this asserts both properties directly, against every concrete subclass,
without needing the backend it is testing to be the one this environment
happens to select.
"""

import inspect

from api.services.filesystem.base import BaseFileSystem
from api.services.filesystem.minio import MinioFileSystem
from api.services.filesystem.null import NullFileSystem
from api.services.filesystem.s3 import S3FileSystem

BACKENDS = (S3FileSystem, MinioFileSystem, NullFileSystem)

#: The reads that exist so an artifact can reach a person. Named explicitly
#: rather than derived, so removing one from the base class does not quietly
#: remove it from this test too.
READ_PATHS = ("aget_signed_url", "aread_bytes", "aopen_range")


class TestTheContractIsWholeEverywhere:
    def test_no_backend_is_left_abstract(self):
        """An unimplemented abstract method is not a missing feature, it is an
        API that will not boot."""
        for backend in BACKENDS:
            missing = getattr(backend, "__abstractmethods__", frozenset())
            assert not missing, (
                f"{backend.__name__} does not implement {sorted(missing)}"
            )

    def test_every_backend_supplies_every_read_path(self):
        for backend in BACKENDS:
            for name in READ_PATHS:
                assert callable(getattr(backend, name, None)), (
                    f"{backend.__name__} is missing {name}"
                )
                assert getattr(backend, name) is not getattr(BaseFileSystem, name), (
                    f"{backend.__name__} inherits {name} instead of implementing it"
                )

    def test_each_read_path_is_part_of_the_contract(self):
        """The decorator drifting off `aread_bytes` is what makes this worth
        asserting: a method nobody is obliged to implement is a method one
        backend will eventually lack, silently."""
        for name in READ_PATHS:
            method = getattr(BaseFileSystem, name)
            assert getattr(method, "__isabstractmethod__", False), (
                f"{name} is no longer abstract, so a backend may omit it silently"
            )

    def test_every_backend_is_async_where_the_base_is(self):
        """A synchronous override would block the event loop that is also
        carrying live calls."""
        for backend in BACKENDS:
            for name in READ_PATHS:
                assert inspect.iscoroutinefunction(
                    getattr(backend, name)
                ) or inspect.isasyncgenfunction(getattr(backend, name)), (
                    f"{backend.__name__}.{name} is not async"
                )
