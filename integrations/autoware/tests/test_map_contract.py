from pathlib import Path
import tempfile
import unittest
from axiom_autoware.map_contract import LaneletMap


def fixture(lights=0):
    nodes="".join(f'<node id="{i}"><tag k="local_x" v="{x}"/><tag k="local_y" v="{y}"/></node>' for i,x,y in ((1,0,-2),(2,10,-2),(3,0,2),(4,10,2),(5,8,3),(6,8,3.2),(7,8,3.5),(8,8,3.7)))
    roads='<way id="10"><nd ref="1"/><nd ref="2"/></way><way id="11"><nd ref="3"/><nd ref="4"/></way><relation id="20"><member type="way" ref="10" role="left"/><member type="way" ref="11" role="right"/><tag k="type" v="lanelet"/><tag k="subtype" v="road"/></relation>'
    signals="".join(f'<way id="{30+i}"><nd ref="{5+i*2}"/><nd ref="{6+i*2}"/></way><relation id="{40+i}"><member type="way" ref="{30+i}" role="refers"/><tag k="type" v="regulatory_element"/><tag k="subtype" v="traffic_light"/></relation>' for i in range(lights))
    return f'<osm>{nodes}{roads}{signals}</osm>'


class MapTests(unittest.TestCase):
    def load(self,folder,lights=0):
        p=Path(folder,"map.osm");p.write_text(fixture(lights));return LaneletMap(p)

    def test_unknown_road_free_and_actual_actor_occupied(self):
        with tempfile.TemporaryDirectory() as folder:
            m=self.load(folder)
            self.assertIn(-1,m.base);self.assertIn(0,m.base);self.assertNotIn(100,m.base)
            grid=m.occupancy([{"pose":{"x":5.,"y":0.,"yaw_rad":0.},"dimensions":{"length":2.,"width":1.,"height":1.}}])
            self.assertIn(100,grid);self.assertNotIn(100,m.base)

    def test_missing_and_ambiguous_signal_map_never_choose_nearest(self):
        signal={"id":"42","pose":{"x":8.,"y":-3.1,"yaw_rad":0.},"state":"Red"}
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.load(folder).map_signals([signal])["status"],"unsupported_map_regulatory_elements_missing")
            exact=self.load(folder,1).map_signals([signal]);self.assertEqual(exact["mapped"][0]["lanelet_id"],40);self.assertEqual(exact["mapped"][0]["state"],"red")
            ambiguous=self.load(folder,2).map_signals([signal]);self.assertEqual(ambiguous["mapped"],[]);self.assertEqual(ambiguous["ambiguous"],["42"])


if __name__=="__main__":unittest.main()
