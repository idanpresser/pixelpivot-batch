import pytest
from app.batch_api.orchestrator import MatrixCell, plan_matrix, output_name

def test_plan_matrix():
    """
    Unit test for plan_matrix: 2x2x1 = 4 cells.
    """
    categories = ["cat1", "cat2"]
    tools = ["tool1", "tool2"]
    formats = ["fmt1"]
    
    plan = plan_matrix(categories, tools, formats)
    assert len(plan) == 4
    assert plan[0] == MatrixCell("cat1", "tool1", "fmt1")
    assert plan[3] == MatrixCell("cat2", "tool2", "fmt1")

def test_output_name_single_category():
    """
    Unit test for output_name when only one category is selected.
    Suffix should be '_tool.fmt'.
    """
    cell = MatrixCell(category="cat1", tool="toolA", target_format="webp")
    name = output_name("image1", cell, multi_category=False)
    assert name == "image1_toolA.webp"

def test_output_name_multi_category():
    """
    Unit test for output_name when multiple categories are selected.
    Suffix should be '_cat_tool.fmt'.
    """
    cell = MatrixCell(category="cat1", tool="toolA", target_format="webp")
    name = output_name("image1", cell, multi_category=True)
    assert name == "image1_cat1_toolA.webp"
