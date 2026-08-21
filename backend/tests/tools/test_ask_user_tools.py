from __future__ import annotations

from schemas.enums import DataClass
from tools.ask_user_tools import AskUserInput, AskUserTool, build_registration_table
from tools.base import ToolContext


async def test_ask_user_echoes_question_and_options_as_typed_output() -> None:
    tool = AskUserTool()

    output = await tool.run(
        AskUserInput(
            question="你問的是台積電（2330）本股，還是它的 ADR（TSM）？",
            options=("本股 2330", "ADR TSM"),
        ),
        ToolContext(),
    )

    assert output.question == "你問的是台積電（2330）本股，還是它的 ADR（TSM）？"
    assert output.options == ("本股 2330", "ADR TSM")


async def test_ask_user_defaults_to_no_options() -> None:
    tool = AskUserTool()

    output = await tool.run(AskUserInput(question="請問是指哪個時間區間？"), ToolContext())

    assert output.options == ()


def test_ask_user_opts_into_the_ask_user_card_type() -> None:
    assert AskUserTool().card_type == "ask_user"
    assert AskUserTool().data_class is DataClass.PUBLIC


def test_build_registration_table_registers_one_deferred_tool() -> None:
    table = build_registration_table()

    assert len(table) == 1
    tool, immediate = table[0]
    assert tool.name == "ask_user"
    assert immediate is False
