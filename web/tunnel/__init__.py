"""Small, platform-neutral Minecraft TCP relay implementation."""

__all__ = ["RelayServer"]


def __getattr__(name):
    # Installation must work before the application dependencies are installed.
    if name == 'RelayServer':
        from .relay import RelayServer
        return RelayServer
    raise AttributeError(name)
