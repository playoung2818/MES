"""Pure production-scope checks: no application startup or database/table creation."""
import ast
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


def workflow_functions():
    source = Path(__file__).resolve().parents[1] / 'app' / 'so_workflow.py'
    nodes = [node for node in ast.parse(source.read_text(encoding='utf-8-sig')).body
             if (isinstance(node, ast.FunctionDef) and node.name in {'production_item', 'execution_source'})
             or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                 and target.id == 'NONPRODUCTION' for target in node.targets))]
    context = {'defaultdict': defaultdict, 'deepcopy': deepcopy}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<scope-test>', 'exec'), context)
    return context


@pytest.mark.parametrize('name', ['Education Discount', ' EDUCATION DISCOUNT ', 'eDuCaTiOn DiScOuNt'])
def test_education_discount_is_not_a_production_item(name):
    functions = workflow_functions()
    assert not functions['production_item']({'item': name})
    assert functions['production_item']({'item': 'System-A'})
    assert functions['production_item']({'item': 'Education Discount Cable'})


def test_discount_is_not_in_mes_execution_source():
    items = [{'id': 'system', 'item': 'System-A', 'quantity': 2},
             {'id': 'discount', 'item': ' Education Discount ', 'quantity': 1}]
    approval = SimpleNamespace(
        to_source=lambda: {'items': deepcopy(items)},
        approved_order_signature={'system-a': 2},
        latest_snapshot={'items': deepcopy(items)}, previous_snapshot=None,
        workflow_status='released_to_mes', source_present=True, reconciliation_details={})
    source = workflow_functions()['execution_source'](approval, [])
    assert [item['item'] for item in source['items']] == ['System-A']
    assert [item['item'] for item in source['fulfillment']] == ['system-a']
    assert not source['reconciliation_required']
