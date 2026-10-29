"""Node room/federation metadata preserves optional assets without changing mods."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.directory import game_metadata


class ResourceMetadataTests(unittest.TestCase):
    def test_optional_metadata_survives_node_validation(self):
        item = dict(name='textures.zip', path='resourcepacks/textures.zip', size=10, sha256='a' * 64)
        result = game_metadata(dict(version='1.21.8', loader='vanilla', resources_count=1,
                                    resources_hash='b' * 64, resources=[item]))
        self.assertEqual(result['resources'], [item])
        self.assertEqual(result['resources_count'], 1)
        self.assertEqual(result['resources_hash'], 'b' * 64)

    def test_untrusted_resource_paths_and_sizes_are_rejected(self):
        item = dict(name='safe.zip', path='shaderpacks/safe.zip', size=1, sha256='a' * 64)
        for changes in ({'path':'../safe.zip'}, {'path':'mods/safe.zip'}, {'name':'../safe.zip'},
                        {'path':'shaderpacks/sub/safe.zip'}, {'size':True}, {'sha256':'bad'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                game_metadata(dict(resources=[dict(item, **changes)]))
