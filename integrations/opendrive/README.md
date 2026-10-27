# Optional authored OpenDRIVE compiler

Install `pip install -e integrations/opendrive` in the API runtime profile that builds maps.
The compiler uses **scenariogeneration 0.16.7**, pinned by this package, to produce the
bounded `object-map/1` road/lane/junction/signal contract. It does not create a CARLA
world or validate physical driving. CARLA world generation occurs in its existing
exclusive worker job. No native SDK is imported by the platform API.

Source map revisions and compiled `/maps` revisions are distinct immutable records.
Source geometry uses CARLA coordinates (right-positive Y); exported OpenDRIVE uses
right-handed coordinates, including the required Y, heading and curvature conversion.
Lane IDs retain OpenDRIVE meaning: negative lanes follow the reference line and
positive lanes travel against it. Maps contain procedural roads only.

Line, arc and clothoid geometries, constant-width driving lanes, explicitly linked
connector roads and signal definitions are supported. Generic imported elements
outside this subset remain read-only with the exact original XML retained. Arbitrary
OpenDRIVE objects do not imply instantiated CARLA collision obstacles; builds with
such objects are blocked until static actors with explicit assets are authored.

## Dependency rights

scenariogeneration is authored by the scenariogeneration contributors and distributed
under MPL-2.0. Upstream source: https://github.com/pyoscx/scenariogeneration
License: https://github.com/pyoscx/scenariogeneration/blob/main/LICENSE
No upstream source is vendored or modified here. Preserve upstream notices if distributing
the dependency and provide its corresponding covered source as required by MPL-2.0.
These code rights do not grant rights in a user's map, engine, assets or standards.
