# Upload service

Accepts file uploads and stores them in object storage.

- [Limits](docs/limits.md): size and time limits on an upload.
- [Operations](docs/operations.md): running and scaling the service.

Uploads larger than 10 MB are rejected, and a request that takes longer than half a minute is cut off.
