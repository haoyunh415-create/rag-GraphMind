import asyncio
import os
import unittest

from app.core import config
from app.retrieval.extractor import extract_from_chunks, extract_query_entities


class EntityExtractorTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        config.get_settings.cache_clear()

    def test_local_extractor_builds_two_hop_relations_without_llm_key(self) -> None:
        os.environ["OPENAI_API_KEY"] = ""
        config.get_settings.cache_clear()

        result = asyncio.run(extract_from_chunks([
            {
                "id": "chunk-1",
                "text": "NovaPayAlpha is owned by OrionLabsAlpha.",
            },
            {
                "id": "chunk-2",
                "text": "OrionLabsAlpha is headquartered in SingaporeAlpha.",
            },
        ]))

        entity_names = {entity["name"] for entity in result.entities}
        relation_pairs = {
            (relation["source"], relation["type"], relation["target"])
            for relation in result.relations
        }

        self.assertIn("NovaPayAlpha", entity_names)
        self.assertIn("OrionLabsAlpha", entity_names)
        self.assertIn("SingaporeAlpha", entity_names)
        self.assertIn(("NovaPayAlpha", "OWNED_BY", "OrionLabsAlpha"), relation_pairs)
        self.assertIn(("OrionLabsAlpha", "HEADQUARTERED_IN", "SingaporeAlpha"), relation_pairs)

    def test_query_entity_fallback_extracts_quoted_multihop_anchor(self) -> None:
        os.environ["OPENAI_API_KEY"] = ""
        config.get_settings.cache_clear()

        entities = asyncio.run(extract_query_entities("Where is \"NovaPayAlpha\" owner's headquarters?"))

        self.assertIn("NovaPayAlpha", entities)


if __name__ == "__main__":
    unittest.main()
