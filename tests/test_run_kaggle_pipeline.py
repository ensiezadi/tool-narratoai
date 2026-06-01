import os
import unittest
from unittest.mock import patch

from run_kaggle_pipeline import configure_kaggle_env


class RunKagglePipelineEnvTests(unittest.TestCase):
    def test_configure_kaggle_env_maps_legacy_token_to_cli_key(self):
        with patch.dict(os.environ, {"KAGGLE_API_TOKEN": "legacy-token"}, clear=True):
            configure_kaggle_env("user")

            self.assertEqual("user", os.environ["KAGGLE_USERNAME"])
            self.assertEqual("legacy-token", os.environ["KAGGLE_KEY"])
            self.assertEqual("legacy-token", os.environ["KAGGLE_API_TOKEN"])

    def test_configure_kaggle_env_prefers_explicit_token(self):
        with patch.dict(os.environ, {"KAGGLE_API_TOKEN": "legacy-token"}, clear=True):
            configure_kaggle_env(kaggle_token="cli-token")

            self.assertEqual("cli-token", os.environ["KAGGLE_KEY"])
            self.assertEqual("cli-token", os.environ["KAGGLE_API_TOKEN"])


if __name__ == "__main__":
    unittest.main()
