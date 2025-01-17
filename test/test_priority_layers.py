# coding=utf-8
"""Tests for the plugin settings manager.

"""

import unittest

from cplus_plugin.conf import settings_manager

from data.priority_weighting_layers import PRIORITY_LAYERS


class PriorityLayersTest(unittest.TestCase):
    """Test the plugins priority layers related operations"""

    def teardown_setup(self):
        self.tearDown()
        self.setUp()

    def test_priority_layers_settings(self):
        """Settings manager can store and retrieve priority layers settings"""

        for layers_list in PRIORITY_LAYERS:
            self.teardown_setup()

            for index, layer in enumerate(layers_list):
                settings_manager.save_priority_layer(layer)
                layer_settings = settings_manager.get_priority_layer(layer.get("uuid"))

                self.assertEqual(layer_settings.get("uuid"), layer.get("uuid"))
                self.assertEqual(layer_settings.get("name"), layer.get("name"))
                self.assertEqual(
                    layer_settings.get("description"), layer.get("description")
                )
                self.assertEqual(layer_settings.get("groups"), layer.get("groups", []))

            self.assertEqual(
                len(settings_manager.get_priority_layers()), len(layers_list)
            )

    def tearDown(self) -> None:
        settings_manager.delete_priority_layers()
