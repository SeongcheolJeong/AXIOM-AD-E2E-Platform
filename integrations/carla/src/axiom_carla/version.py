ADAPTER_VERSION = "0.8.0"

# Read compatibility is explicit and distinct from permission to dispatch. An
# old execution keeps its original request, version, checkpoint and archive.
DURABLE_READ_VERSIONS = ("0.5.0", "0.6.0", "0.7.0", "0.8.0")


def require_durable_version(version, *, dispatch=False):
    if not isinstance(version, str) or version not in ((ADAPTER_VERSION,) if dispatch else DURABLE_READ_VERSIONS):
        raise ValueError("Unsupported durable CARLA adapter version")
    return version
