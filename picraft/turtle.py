# vim: set et sw=4 sts=4 fileencoding=utf-8:
#
# An alternate Python Minecraft library for the Rasperry-Pi
# Copyright (c) 2013-2016 Dave Jones <dave@waveform.org.uk>
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

from __future__ import (
    unicode_literals,
    absolute_import,
    print_function,
    division,
    )
str = type('')


import re
import os
import math
import inspect
from threading import Lock, local
from collections import namedtuple

from .world import World
from .vector import Vector, O, X, Y, Z, vector_range, line
from .block import Block


class TurtleCache(object):
    """
    A representation of the world's blocks with implicit, thread-safe caching.

    Using the cache as a context manager starts a batch operation which builds
    up state changes until the termination of the ``with`` block. If the block
    is terminated without an exception being raised, all changes are applied
    to the world.

    Requesting the state of blocks will always read from the cache and, if
    a batch is active, from the (as yet uncommitted) changes made by the batch.
    Note that batches are stored in thread-local state.
    """
    def __init__(self, world):
        self._world = world
        self._lock = Lock()
        self._cache = {}
        self._batch = local()

    def __enter__(self):
        try:
            self._batch.level += 1
        except AttributeError:
            self._batch.level = 1
            self._batch.state = {}
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self._batch.level -= 1
        if self._batch.level == 0:
            state = self._batch.state
            del self._batch.level, self._batch.state
            if exc_type is None:
                self.__setitem__(state.keys(), state.values())

    def __getitem__(self, positions):
        try:
            # no need for thread lock, as we're getting a thread local
            batch = self._batch.state
        except AttributeError:
            batch = {} # no active batch
        with self._lock:
            unknown = set(positions) - set(self._cache.keys()) - set(batch.keys())
            if unknown:
                self._cache.update({
                    v: b
                    for v, b in zip(unknown, self._world.blocks[unknown])
                    })
            return {
                v: batch.get(v, self._cache[v])
                for v in positions
                }

    def __setitem__(self, positions, blocks):
        try:
            # no need for thread lock, as we're updating a thread local
            self._batch.state.update({v: b for (v, b) in zip(positions, blocks)})
        except AttributeError:
            with self._lock:
                diff = {
                    v: b
                    for v, b in zip(positions, blocks)
                    if b != self._cache[v]
                    }
                with self._world.connection.batch_start():
                    self._world.blocks[diff.keys()] = diff.values()
                self._cache.update(diff)


class TurtleScreen(object):
    def __init__(self, world=None):
        if world is None:
            world = _default_world()
        self._world = world
        self._blocks = TurtleCache(world)

    @property
    def world(self):
        return self._world

    @property
    def blocks(self):
        return self._blocks

    def draw(self, state):
        self.blocks[state.keys()] = state.values()

    def chat(self, message):
        self._world.say(message)


TurtleState = namedtuple('TurtleState', (
    'position',  # Vector
    'heading',   # Vector
    'elevation', # angle (-90..90)
    'visible',   # bool
    'pendown',   # bool
    'penblock',  # Block
    'fillblock', # Block
    'changed',   # Vector->Block map
    'action',    # home/move/line/turtle
    ))

clamp = lambda value, min_value, max_value: min(max_value, max(min_value, value))


class Turtle(object):
    def __init__(self, screen=None, pos=None):
        if screen is None:
            screen = _default_screen()
        if pos is None:
            pos = screen.world.player.tile_pos - Y
        self._screen = screen
        self._state = TurtleState(
            position=pos,
            heading=Z,
            elevation=0.0,
            visible=True,
            pendown=True,
            penblock=Block('stone'),
            fillblock=Block('stone'),
            changed={},
            action='home',
            )
        self._last_position = self._state.position
        self._history = [self._state] # undo buffer
        self._draw_turtle()

    def _commit(self, changes, action):
        """
        Given *changes*, a mapping of vectors to blocks, and *action*, a string
        describing the change, append a new state to the undo history with the
        original state of the affected blocks, and draw the changes to the
        screen.
        """
        self._history.append(self._state._replace(
            changed=self._screen.blocks[changes.keys()], # reverse diff
            action=action
            ))
        if changes:
            self._screen.draw(changes)

    def _draw_vectors(self):
        """
        Calculates and returns the arm and head unit vectors based on the
        current heading and elevation.
        """
        arm_v = self._state.heading.cross(Y).unit
        if arm_v == O:
            arm_v = X
        head_v = self._state.heading.rotate(self._state.elevation, about=arm_v)
        return arm_v, head_v

    def _draw_turtle(self):
        """
        Draw the turtle's head and arms, and the pen block, committing the
        resulting changes as an undo history entry with the action "turtle".
        """
        arm_v, head_v = self._draw_vectors()
        head = (self._state.position + head_v).round()
        left_arm = (self._state.position + arm_v).round()
        right_arm = (self._state.position - arm_v).round()
        state = {
            v: Block('wool', 15)
            for v in (head, left_arm, right_arm)
            }
        if self._state.pendown:
            state[self._state.position] = self._state.penblock
        self._commit(state, 'turtle')

    def _undraw_turtle(self):
        """
        If the last action in the undo history is "turtle" (indicating that the
        last action taken was to draw the turtle), remove it and revert the
        affected blocks to their prior state.
        """
        with self._screen.blocks:
            while self._history and self._history[-1].action == 'turtle':
                self._screen.draw(self._history.pop().changed)

    def _update(self):
        """
        Commit the difference between the ephemeral state (``_state``) and the
        last recorded position to the undo history as a line (if the pen is
        down) or a move (if it's not).
        """
        with self._screen.blocks:
            self._undraw_turtle()
            if self._state.pendown and self._state.position != self._last_position:
                self._commit({
                    v: self._state.penblock
                    for v in line(self._last_position, self._state.position)
                    }, 'line')
            else:
                self._commit({}, 'move')
            if self._state.visible:
                self._draw_turtle()
            self._last_position = self._state.position

    def undo(self):
        """
        Undo (repeatedly) the last turtle action(s)::

            >>> for i in range(4):
            ...     turtle.fd(5)
            ...     turtle.lt(90)
            ...
            >>> for i in range(8):
            ...     turtle.undo()
        """
        with self._screen.blocks:
            self._undraw_turtle()
            if self._history[-1].action != 'home':
                self._screen.draw(self._history.pop().changed)
                self._state = self._history[-1]
                self._last_position = self._state.position
            if self._state.visible:
                self._draw_turtle()

    def home(self):
        """
        Move the turtle to its starting position (this is usually beneath where
        the player was standing when the turtle was spawned), and set its
        heading to its start orientation (0 degrees heading, 0 degrees
        elevation)::

            >>> turtle.heading()
            90.0
            >>> turtle.elevation
            45.0
            >>> turtle.position()
            Vector(x=2, y=-1, z=16)
            >>> turtle.home()
            >>> turtle.position()
            Vector(x=0, y=-1, z=0)
            >>> turtle.heading()
            0.0
            >>> turtle.elevation()
            0.0
        """
        self._state = self._state._replace(
            position=self._history[0].position,
            heading=Z,
            elevation=0.0,
            )
        self._update()

    def clear(self):
        with self._screen.blocks:
            while self._history[-1].action != 'home':
                self._screen.draw(self._history.pop().changed)
            self._update()

    def reset(self):
        with self._screen.blocks:
            self.clear()
            self._last_position = self._history[0].position
            self.home()

    def pos(self):
        """
        Return the turtle's current location (x, y, z) as a
        :class:`~picraft.vector.Vector`::

            >>> turtle.pos()
            Vector(x=2, y=-1, z=18)
        """
        return self._state.position

    def xcor(self):
        """
        Return the turtle's x coordinate::

            >>> turtle.home()
            >>> turtle.xcor()
            0
            >>> turtle.left(90)
            >>> turtle.forward(2)
            >>> turtle.xcor()
            2
        """
        return self._state.position.x

    def ycor(self):
        """
        Return the turtle's y coordinate::

            >>> turtle.home()
            >>> turtle.ycor()
            -1
            >>> turtle.up(90)
            >>> turtle.forward(2)
            >>> turtle.ycor()
            1
        """
        return self._state.position.y

    def zcor(self):
        """
        Return the turtle's z coordinate::

            >>> turtle.home()
            >>> turtle.zcor()
            0
            >>> turtle.forward(2)
            >>> turtle.zcor()
            2
        """
        return self._state.position.z

    def towards(self, x, y=None, z=None):
        """
        :param float x: the target x coordinate or a turtle / triple /
                        :class:`~picraft.vector.Vector` of numbers
        :param float y: the target y coordinate or ``None``
        :param float z: the target z coordinate or ``None``

        Return the angle between the line from the turtle's position to the
        position specified within the ground plane (X-Z)::

            >>> turtle.home()
            >>> turtle.forward(5)
            >>> turtle.towards(0, 0, 0)
            -180.0
            >>> turtle.left(90)
            >>> turtle.forward(5)
            >>> turtle.towards(0, 0, 0)
            135.0

        If *y* and *z* are ``None``, *x* must be a triple of coordinates, a
        :class:`~picraft.vector.Vector`, or another Turtle.
        """
        if isinstance(x, Turtle):
            other = x.pos()
        else:
            try:
                x, y, z = x
            except (TypeError, ValueError) as exc:
                pass
            other = Vector(x, y, z)
        v = (other - self._state.position).replace(y=0).unit
        return math.degrees(math.atan2(-v.x, v.z))

    def goto(self, x, y=None, z=None):
        """
        :param float x: the new x coordinate or a turtle / triple /
                        :class:`~picraft.vector.Vector` of numbers
        :param float y: the new y coordinate or ``None``
        :param float z: the new z coordinate or ``None``

        Moves the turtle to an absolute position. If the pen is down, draws
        a line between the current position and the newly specified position.
        Does not change the turtle's orientation::

            >>> tp = turtle.pos()
            >>> tp
            Vector(x=2, y=-1, z=16)
            >>> turtle.setpos(4, -1, 16)
            >>> turtle.pos()
            Vector(x=4, y=-1, z=16)
            >>> turtle.setpos((0, -1, 16))
            >>> turtle.pos()
            Vector(x=0, y=-1, z=16)
            >>> turtle.setpos(tp)
            >>> turtle.pos()
            Vector(x=2, y=-1, z=16)

        If *y* and *z* are ``None``, *x* must be a triple of coordinates, a
        :class:`~picraft.vector.Vector`, or another Turtle.
        """
        if isinstance(x, Turtle):
            other = x.pos()
        else:
            try:
                x, y, z = x
            except (TypeError, ValueError) as exc:
                pass
            other = Vector(x, y, z)
        self._state = self._state._replace(position=other)
        self._update()

    def setx(self, x):
        """
        :param float x: the new x coordinate

        Set the turtle's first coordinate to *x*; leave the second and third
        coordinates unchanged::

            >>> turtle.position()
            Vector(x=2, y=-1, z=16)
            >>> turtle.setx(5)
            >>> turtle.position()
            Vector(x=5, y=-1, z=16)
        """
        self.goto(pos().replace(x=x))

    def sety(self, y):
        """
        :param float y: the new y coordinate

        Set the turtle's second coordinate to *y*; leave the first and third
        coordinates unchanged::

            >>> turtle.position()
            Vector(x=2, y=-1, z=16)
            >>> turtle.sety(5)
            >>> turtle.position()
            Vector(x=2, y=5, z=16)
        """
        self.goto(pos().replace(y=y))

    def setz(self, z):
        """
        :param float z: the new z coordinate

        Set the turtle's third coordinate to *z*; leave the first and second
        coordinates unchanged::

            >>> turtle.position()
            Vector(x=2, y=-1, z=16)
            >>> turtle.setz(5)
            >>> turtle.position()
            Vector(x=2, y=-1, z=5)
        """
        self.goto(pos().replace(z=z))

    def distance(self, x, y=None, z=None):
        """
        :param float x: the target x coordinate or a turtle / triple /
                        :class:`~picraft.vector.Vector` of numbers
        :param float y: the target y coordinate or ``None``
        :param float z: the target z coordinate or ``None``

        Return the distance from the turtle to (x, y, z), the given vector, or
        the given other turtle, in blocks::

            >>> turtle.home()
            >>> turtle.distance((0, -1, 5))
            5.0
            >>> turtle.forward(2)
            >>> turtle.distance(0, -1, 5)
            3.0
        """
        if isinstance(x, Turtle):
            other = x.pos()
        else:
            try:
                x, y, z = x
            except (TypeError, ValueError) as exc:
                pass
            other = Vector(x, y, z)
        return self._state.position.distance_to(other)

    def elevation(self):
        """
        Return the turtle's current elevation (its orientation away from the
        ground plane, X-Z)::

            >>> turtle.home()
            >>> turtle.up(90)
            >>> turtle.elevation()
            90.0
        """
        return self._state.elevation

    def heading(self):
        """
        Return the turtle's current heading (its orientation along the ground
        plane, X-Z)::

            >>> turtle.home()
            >>> turtle.right(90)
            >>> turtle.heading()
            90.0
        """
        result = self._state.heading.angle_between(Z)
        if self._state.heading.cross(Z).y < 0:
            result += 180
        return result

    def setelevation(self, to_angle):
        """
        :param float to_angle: the new elevation

        Set the elevation of the turtle away from the ground plane (X-Z) to
        *to_angle*. At 0 degrees elevation, the turtle moves along the ground
        plane (X-Z). At 90 degrees elevation, the turtle moves vertically
        upward, and at -90 degrees, the turtle moves vertically downward::

            >>> turtle.setelevation(90)
            >>> turtle.elevation()
            90.0
        """
        self._state = self._state._replace(elevation=clamp(to_angle, -90, 90))
        self._update()

    def setheading(self, to_angle):
        """
        :param float to_angle: the new heading

        Set the orientation of the turtle on the ground plane (X-Z) to
        *to_angle*. The common directions in degrees correspond to the
        following axis directions:

        ======= ====
        heading axis
        ======= ====
        0       +Z
        90      +X
        180     -Z
        270     -X
        ======= ====

        ::

            >>> turtle.setheading(90)
            >>> turtle.heading()
            90.0
        """
        self._state = self._state._replace(heading=Z.rotate(to_angle, about=Y))
        self._update()

    def forward(self, distance):
        """
        :param float distance: the number of blocks to move forward.

        Move the turtle forward by the specified *distance*, in the direction
        the turtle is headed::

            >>> turtle.position()
            Vector(x=2, y=-1, z=13)
            >>> turtle.forward(5)
            >>> turtle.position()
            Vector(x=2, y=-1, z=18)
            >>> turtle.forward(-2)
            >>> turtle.position()
            Vector(x=2, y=-1, z=16)
        """
        arm_v, head_v = self._draw_vectors()
        self._state = self._state._replace(
            position=(self._state.position + distance * head_v).round()
            )
        self._update()

    def backward(self, distance):
        """
        :param float distance: the number of blocks to move back.

        Move the turtle backward by the specified *distance*, opposite to the
        direction the turtle is headed. Does not change the turtle's heading::

            >>> turtle.heading()
            0.0
            >>> turtle.position()
            Vector(x=2, y=-1, z=18)
            >>> turtle.backward(2)
            >>> turtle.position()
            Vector(x=2, y=-1, z=16)
            >>> turtle.heading()
            0.0
        """
        arm_v, head_v = self._draw_vectors()
        self._state = self._state._replace(
            position=(self._state.position - distance * head_v).round()
            )
        self._update()

    def right(self, angle):
        """
        :param float angle: the number of degrees to turn clockwise.

        Turns the turtle right (clockwise) by *angle* degrees::

            >>> turtle.heading()
            0.0
            >>> turtle.right(90)
            >>> turtle.heading()
            90.0
        """
        self._state = self._state._replace(
            heading=self._state.heading.rotate(-angle, about=Y)
            )
        self._update()

    def left(self, angle):
        """
        :param float angle: the number of degrees to turn counter clockwise.

        Turns the turtle left (counter-clockwise) by *angle* degrees::

            >>> turtle.heading()
            90.0
            >>> turtle.left(90)
            >>> turtle.heading()
            0.0
        """
        self._state = self._state._replace(
            heading=self._state.heading.rotate(angle, about=Y)
            )
        self._update()

    def down(self, angle):
        """
        :param float angle: the number of degrees to reduce elevation by.

        Turns the turtle's nose (its elevation) down by *angle* degrees::

            >>> turtle.elevation()
            0.0
            >>> turtle.down(45)
            >>> turtle.elevation()
            -45.0
        """
        self._state = self._state._replace(
            elevation=clamp(self._state.elevation - angle, -90, 90)
            )
        self._update()

    def up(self, angle):
        """
        :param float angle: the number of degrees to increase elevation by.

        Turns the turtle's nose (its elevation) up by *angle* degrees::

            >>> turtle.elevation()
            -45.0
            >>> turtle.up(45)
            >>> turtle.elevation()
            0.0
        """
        self._state = self._state._replace(
            elevation=clamp(self._state.elevation + angle, -90, 90)
            )
        self._update()

    def isdown(self):
        """
        Returns ``True`` if the pen is down, ``False`` if it's up.
        """
        return self._state.pendown

    def pendown(self):
        """
        Put the "pen" down; the turtle draws new blocks when it moves.
        """
        self._state = self._state._replace(pendown=True)
        self._update()

    def penup(self):
        """
        Put the "pen" up; movement doesn't draw new blocks.
        """
        self._state = self._state._replace(pendown=False)
        self._update()

    def isvisible(self):
        return self._state.visible

    def showturtle(self):
        self._state = self._state._replace(visible=True)
        self._update()

    def hideturtle(self):
        self._state = self._state._replace(visible=False)
        self._update()

    def block(self, *args):
        if not args:
            return self.penblock(), self.fillblock()
        else:
            self.penblock(*args)
            self.fillblock(*args)

    def penblock(self, *args):
        """
        Return or set the block that the turtle draws when it moves. Several
        input formats are allowed:

        ``penblock()``
            Return the current pen block. May be used as input to another
            penblock or fillblock call.

        ``penblock(Block('grass'))``
            Set the pen block to the specified :class:`~picraft.block.Block`
            instance.

        ``penblock('grass')``
            Implicitly make a :class:`~picraft.block.Block` from the given
            arguments and set that as the pen block.

        ::

            >>> turtle.penblock()
            <Block "stone" id=1 data=0>
            >>> turtle.penblock('diamond_block')
            >>> turtle.penblock()
            <Block "diamond_block" id=57 data=0>
            >>> turtle.penblock(1, 0)
            >>> turtle.penblock()
            <Block "stone" id=1 data=0>
        """
        if not args:
            return self._state.penblock
        else:
            if isinstance(args[0], Block):
                self._state = self._state._replace(penblock=args[0])
            else:
                self._state = self._state._replace(penblock=Block(*args))
            self._update()

    def fillblock(self, *args):
        if not args:
            return self._state.fillblock
        else:
            if isinstance(args[0], Block):
                self._state = self._state._replace(fillblock=args[0])
            else:
                self._state = self._state._replace(fillblock=Block(*args))
            self._update()

    position = pos
    setpos = goto
    setposition = goto
    sete = setelevation
    seth = setheading
    fd = forward
    bk = backward
    back = backward
    rt = right
    lt = left
    dn = down
    st = showturtle
    ht = hideturtle
    pd = pendown
    pu = penup


class TurtlePlayer(object):
    def __init__(self, screen=None, player_id=None):
        if screen is None:
            screen = _default_screen()
        self._screen = screen
        if player_id is None:
            self._player = self._screen.world.player
        else:
            self._player = self._screen.world.players[player_id]

    def where(self):
        return self._player.pos

    def teleport(self, x, y=None, z=None):
        if isinstance(x, Turtle):
            other = x.pos() + Y
        else:
            try:
                x, y, z = x
            except (TypeError, ValueError) as exc:
                pass
            other = Vector(x, y, z)
        self._player.pos = other

    def jump(self, height=2):
        self.teleport(self._player.pos + height * Y)


# default objects constructed when the straight function interface is used

_WORLD = None # The global World() used by default
_SCREEN = None # The global TurtleScreen() used by default
_TURTLE = None # The global Turtle() used by default
_PLAYER = None # The global TurtlePlayer() used by default

def _default_world():
    global _WORLD
    if _WORLD is None:
        _WORLD = World()
    return _WORLD

def _default_screen():
    global _SCREEN
    if _SCREEN is None:
        _SCREEN = TurtleScreen()
    return _SCREEN

def _default_turtle():
    global _TURTLE
    if _TURTLE is None:
        _TURTLE = Turtle()
    return _TURTLE

def _default_player():
    global _PLAYER
    if _PLAYER is None:
        _PLAYER = TurtlePlayer()
    return _PLAYER

def _method_to_func(name, method, factory):
    """
    Creates a procedural variant of *method* on the instance returned by
    calling *factory*.
    """
    template = """\
def {name}{defargs}:
    return {factory}().{name}{callargs}"""
    spec = inspect.getargspec(method)
    defargs = inspect.formatargspec(spec.args[1:], spec.varargs, spec.keywords, spec.defaults)
    callargs = inspect.formatargspec(spec.args[1:], spec.varargs, spec.keywords, ())
    exec(template.format(
        name=name,
        defargs=defargs,
        callargs=callargs,
        factory=factory.__name__,
        ), globals())
    # If the method has a doc-string, copy it to the new function ... but only
    # when the method isn't an alias (name==method.__name__) or we're not
    # building the picraft docs (in which we don't want to repeat all the docs
    # for the aliases)
    if method.__doc__ is not None:
        if not 'PICRAFTDOCS' in os.environ or name == method.__name__:
            # Replace "turtle." in all the examples with a blank string
            globals()[name].__doc__ = re.sub(
                r'^( *(?:>>>|\.\.\.).*)turtle\.', r'\1',
                method.__doc__, flags=re.MULTILINE)

def _classes_to_funcs():
    """
    Uses :func:`_method_to_func` to construct procedural variants of the
    :class:`Turtle`, :class:`TurtleScreen`, and :class:`TurtlePlayer` class'
    methods.
    """
    for method in dir(Turtle):
        if not method.startswith('_'):
            _method_to_func(method, getattr(Turtle, method), _default_turtle)
    for method in dir(TurtlePlayer):
        if not method.startswith('_'):
            _method_to_func(method, getattr(TurtlePlayer, method), _default_player)
    _method_to_func('chat', TurtleScreen.chat, _default_screen)

_classes_to_funcs()
