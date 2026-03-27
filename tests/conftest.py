import pytest
import importlib
import jieba

from src.vocab.lookup import VocabLookup
from src.segmentation.segmenter import ChineseSegmenter
from src.segmentation.classifier import LevelClassifier


@pytest.fixture(scope="session", autouse=True)
def reset_jieba():
    """Reset jieba state to ensure clean HSK dictionary loading."""
    jieba.dt.initialized = False
    importlib.reload(jieba)
    ChineseSegmenter._initialized = False


@pytest.fixture(scope="session")
def vocab_lookup():
    return VocabLookup()


@pytest.fixture(scope="session")
def segmenter():
    return ChineseSegmenter()


@pytest.fixture(scope="session")
def classifier(vocab_lookup, segmenter):
    return LevelClassifier(vocab_lookup, segmenter)


# Sample texts for testing
PURE_HSK1_TEXT = "我是学生。我在学校学习。今天天气很好。我很高兴。"
MOSTLY_HSK1_TEXT = "我是学生。我在学校学习中文。老师很好。我喜欢我的朋友。"
HSK2_TEXT = "今天是周末。我不用上班。我和朋友一起去公园。天气不错，我们走了很久。"
MIXED_TEXT = "人工智能技术正在改变教育的方式。学生可以在网上学习。"
