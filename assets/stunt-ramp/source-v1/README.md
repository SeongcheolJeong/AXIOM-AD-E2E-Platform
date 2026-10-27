# Stunt Ramp source v1

This is original project-authored geometry: an 8 m wide, 6 m radius, 110 degree
ramp with a 12 m approach and a separate 30 x 30 m landing slab. The ramp is
0.3 m thick; its curved face has 55 segments. The landing surface is z=-0.3 m,
below the approach's z=0 m surface. The top overhang ends about 8.05 m above
the approach. These are authored dimensions, not a Tesla test-site replica.

`visual.obj` contains the three visible watertight solids. `collision.obj`
contains a separate convex hull for each ramp strip and each slab. The names
follow the intended UCX mesh association; OBJ names alone do not install
collision in Unreal. `manifest.json` pins the generator and mesh hashes.

Coordinates are meters, X forward, Y right, Z up. Import all mesh objects in a
DCC tool with names/transforms preserved; export a compatible FBX containing
the visual meshes and their matching UCX hulls. Convert meters to Unreal
centimeters exactly once (factor 100). In a compatible CARLA source/editor
build, import each visual mesh with its matching hulls as simple collision,
place actors at the common origin with static mobility, and cook a dedicated
map/package. Do not enable automatic collision that replaces the supplied
hulls without recording it. Verify normals, the approach seam, overhang
collision, contact normals, tire contact and dropped/rotated vehicle response.
Record editor/server versions and cooked map/collision package hashes.

No editor import, FBX export, engine cooking or native collision verification
has happened merely because this bundle exists. Ramp execution must remain
blocked until that evidence and the suction/contact model are implemented.
`physical_validation: not_established` applies to this source and future
simulation. No claim is made about real vehicles or safe landings.

Rights: original project-authored geometry; no external meshes or textures.
The project's distribution license is undecided (repository README), so this
bundle does not grant a new external asset license. CARLA assets and Unreal
tools retain their separate license requirements.

Regenerate into a new directory with:
`python scripts/build_stunt_ramp_asset.py --output <new-directory>`
