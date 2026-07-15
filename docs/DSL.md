
# DSL

## Dataclasses

### Board


## Operations

### Board

#### View Board 

    - [ ] ViewBoard --> (list[Column] + board name), CreateBoard, RenameBoard, DeleteBoard.
    - [ ] MinimizeColumn, MaximizeColumn, CreateColumn, SwapColumn (column, column_to_swap_with) -> (), DeleteColumn, RenameColumn.
    - [ ] ViewItem, CreateItem, EditItem, DeleteItem, MoveItemVertical (item, direction, item_jumped_over) -> (), MoveItemHorizontal (item, adjacent_column, adjacent_column_item_to_push).
    - [ ] ChangeKeybinding (action_key, new_binding).
    - [ ] ChangeSetting (setting_key, new_value).
