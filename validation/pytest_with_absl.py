import sys
from absl import flags
from absl.testing import absltest
import pytest
flags.FLAGS([sys.argv[0]])
sys.exit(pytest.main(sys.argv[1:]))
