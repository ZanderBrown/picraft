# vim: set et sw=4 sts=4 fileencoding=utf-8:
#
# An alternate Python Minecraft library for the Rasperry-Pi
# Copyright (c) 2013-2015 Dave Jones <dave@waveform.org.uk>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the copyright holder nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""
The world module defines the :class:`World` class, which is the usual way of
starting a connection to a Minecraft server and which then provides various
attributes allowing the user to query and manipulate that world.

.. note::

    All items in this module are available from the :mod:`picraft` namespace
    without having to import :mod:`picraft.world` directly.

The following items are defined in the module:


World
=====

.. autoclass:: World
    :members:


Checkpoint
==========

.. autoclass:: Checkpoint
    :members:
"""

from __future__ import (
    unicode_literals,
    absolute_import,
    print_function,
    division,
    )
str = type('')


from .connection import Connection
from .player import Player, HostPlayer, Players
from .block import Blocks


class World(object):
    """
    Represents a Minecraft world.

    This is the primary class that users interact with. Construct an instance
    of this class, optionally specifying the *host* and *port* of the server
    (which default to "localhost" and 4711 respectively). Afterward, the
    instance can be used to query and manipulate the minecraft world of the
    connected game.

    The :meth:`say` method can be used to send commands to the console, while
    the :attr:`player` attribute can be used to manipulate or query the status
    of the player character in the world. The :attr:`players` attribute can be
    used to manipulate or query other players within the world (this object can
    be iterated over to discover players)::

        >>> from picraft import *
        >>> world = World()
        >>> len(world.players)
        1
        >>> world.say('Hello, world!')
    """

    def __init__(
            self, host='localhost', port=4711, timeout=0.2,
            ignore_errors=False):
        self._connection = Connection(host, port, timeout, ignore_errors)
        self._player = HostPlayer(self._connection)
        self._players = Players(self._connection)
        self._blocks = Blocks(self._connection)

    @property
    def connection(self):
        """
        Represents the connection to the Minecraft server.

        The :class:`~picraft.connection.Connection` object contained in this
        attribute represents the connection to the Minecraft server and
        provides various methods for communicating with it. Users will very
        rarely need to access this attribute, except to use the
        :meth:`~picraft.connection.Connection.batch_start` method.
        """
        return self._connection

    @property
    def players(self):
        """
        Represents all player entities in the Minecraft world.

        This property can be queried to determine which players are currently
        in the Minecraft world. The property is a mapping of player id (an
        integer number) to a :class:`~picraft.player.Player` object which
        permits querying and manipulation of the player. The property supports
        many of the methods of dicts and can be iterated over like a dict::

            >>> len(world.players)
            1
            >>> list(world.players)
            [1]
            >>> world.players.keys()
            [1]
            >>> world.players.values()
            [<picraft.player.Player at 0x7f2f91f38cd0>]
            >>> world.players.items()
            [(1, <picraft.player.Player at 0x7f2f91f38cd0>)]
            >>> for player in world.players:
            ...     print(player.tile_pos)
            ...
            -3,18,-5
        """
        return self._players

    @property
    def player(self):
        """
        Represents the host player in the Minecraft world.

        The :class:`~picraft.player.HostPlayer` object returned by this
        attribute provides properties which can be used to query the status of,
        and manipulate the state of, the host player in the Minecraft world::

            >>> world.player.pos
            Vector(x=-2.49725, y=18.0, z=-4.21989)
            >>> world.player.tile_pos += Vector(y=50)
        """
        return self._player

    @property
    def blocks(self):
        """
        Represents the state of blocks in the Minecraft world.

        This property can be queried to determine the type of a block in the
        world, or can be set to alter the type of a block. The property can be
        indexed with a single :class:`Vector`, in which case the state of a
        single block is returned (or updated) as a :class:`Block` object::

            >>> world.blocks[g.player.tile_pos]
            <Block "grass" id=2 data=0>

        Alternatively, a slice of two vectors can be used. In this case, when
        querying the property, a sequence of :class:`Block` objects is
        returned, When setting a slice of two vectors you can either pass a
        sequence of :class:`Block` objects or a single :class:`Block` object.
        The sequence must be equal to the number of blocks represented by the
        slice::

            >>> world.blocks[Vector(0,0,0):Vector(2,1,1)]
            [<Block "grass" id=2 data=0>,<Block "grass" id=2 data=0>]
            >>> world.blocks[Vector(0,0,0):Vector(5,1,5)] = Block.from_name('grass')

        As with normal Python slices, the interval specified is `half-open`_.
        That is to say, it is inclusive of the lower vector, *exclusive* of the
        upper one. Hence, ``Vector():Vector(x=5,1,1)`` represents the
        coordinates (0,0,0) to (4,0,0).

        .. _half-open: http://python-history.blogspot.co.uk/2013/10/why-python-uses-0-based-indexing.html

        .. warning:

            Querying or setting sequences of blocks is extremely slow as a
            network transaction must be executed for each individual block.
            When setting a slice of blocks, this can be speeded up by
            specifying a single :class:`Block` in which case one network
            transaction will occur to set all blocks in the slice.
            Additionally, a :meth:`connection batch
            <picraft.connection.Connection.batch_start>` can be used to speed
            things up.
        """
        return self._blocks

    def checkpoint(self):
        """
        Represents the Minecraft world checkpoint system.

        The :class:`Checkpoint` object returned by this attribute provides
        the ability to save and restore the state of the world at any time::

            >>> world.checkpoint.save()
            >>> world.blocks[Vector()] = Block.from_name('stone')
            >>> world.checkpoint.restore()
        """
        return self._checkpoint

    def close(self):
        """
        Closes the connection to the game server.

        After this method is called, the connection is closed and no further
        requests can be made. This method is implicitly called when the class
        is used as a context manager.
        """
        self.connection.close()

    def say(self, message):
        """
        Displays *message* in the game's chat console.

        The *message* parameter must be a string (which may contain multiple
        lines). Each line of the message will be sent to the game's chat
        console and displayed immediately. For example::

            >>> world.say('Hello, world!')
            >>> world.say('The following player IDs exist:\\n%s' %
            ...     '\n'.join(str(p) for p in world.players))
        """
        for line in message.splitlines():
            self.connection.send('chat.post(%s)' % line)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.close()


class Checkpoint(object):
    """
    Permits restoring the world state from a prior save.

    This class provides methods for storing the state of the Minecraft world,
    and restoring the saved state at a later time. The :meth:`save` method
    saves the state of the world, and the :meth:`restore` method restores
    the saved state.

    This class can be used as a context manager to take a checkpoint, make
    modifications to the world, and roll them back if an exception occurs.
    For example, the following code will ultimately do nothing because an
    exception occurs after the alteration::

        >>> from picraft import *
        >>> w = World()
        >>> with w.checkpoint:
        ...     w.blocks[w.player.tile_pos - Vector(y=1)] = Block.from_name('stone')
        ...     raise Exception()

    .. warning::

        Minecraft only permits a single checkpoint to be stored at any given
        time. There is no capability to save multiple checkpoints and no way of
        checking whether one currently exists. Therefore, storing a checkpoint
        may overwrite an older checkpoint without warning.

    .. note::
        Checkpoints don't work *within* batches as the checkpoint save will be
        batched along with everything else. That said, a checkpoint can be used
        *outside* a batch to roll the entire thing back if it fails::

            >>> v = w.player.tile_pos - Vector(y=1)
            >>> with w.checkpoint:
            ...     with w.connection.batch_start():
            ...         w.blocks[v - Vector(2, 0, 2):v + Vector(2, 1, 2)] = [
            ...             Block.from_name('wool', data=i) for i in range(16)]
    """

    def __init__(self, connection):
        self._connection = connection

    def save(self):
        """
        Save the state of the Minecraft world, overwriting any prior checkpoint
        state.
        """
        self._connection.send('world.checkpoint.save()')

    def restore(self):
        """
        Restore the state of the Minecraft world from a previously saved
        checkpoint.  No facility is provided to determine whether a prior
        checkpoint is available (the underlying network protocol doesn't permit
        this).
        """
        self._connection.send('world.checkpoint.restore()')

    def __enter__(self):
        self.save()

    def __exit__(self, exc_type, exc_value, exc_tb):
        if exc_type is not None:
            self.restore()

