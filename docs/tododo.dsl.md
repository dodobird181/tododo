# Tododo Domain

The tododo domain in the [DSL Format](DSL-format.md) notation.

**Event-sourced.** The source of truth is an append-only **event log**: one
immutable `Event` per file under `events/`, named by its unique `id`. App state
(`Item`, `Board`) is a *projection* — replay every event from a zero state and
fold it in. Because every event has a unique filename, two people editing the
same data produce two different files, so **git never sees a merge conflict**.

A *real* conflict (two concurrent edits to the same field) is detected at
**replay** time via each event's `parent` pointer and surfaced to the UI as a
`Conflict` for the user to resolve — resolution is just another event. See
[DSL.md](DSL.md) for the original operation sketch.

## Types

### Event
The only thing persisted. Immutable; filename == `id`.
```dsl
id:      Id                 // unique -> unique filename -> no git merge conflict
at:      Date               // ISO datetime, wall-clock of the author
by:      Text               // github login
op:      Text               // operation name, e.g. "EditItem"
target:  Id                 // the Item or Board id this event mutates
field:   Text?              // field touched, "" for whole-target ops (create/delete)
parent:  @Event.id?         // the event this one was based on (per target+field)
payload: {key: Text}        // operation arguments
```

`parent` is the lineage edge (like a git parent commit). Two events with the
**same `parent`** touching the **same `target`+`field`** are *concurrent* → a
conflict. An event whose `parent` is an ancestor of another's is not.

### Conflict
Not persisted — computed during replay, consumed by the UI.
```dsl
target: Id                  // Item/Board in dispute
field:  Text                // the field with divergent edits
events: @Event[]            // the 2+ concurrent events, each a candidate value
```

### Field
A projected value plus the event that last won it.
```dsl
value:          Text
last_edited_at: Date        // from the winning Event.at
last_edited_by: Text        // from the winning Event.by
event:          @Event.id   // provenance: which event set this value
```

### Item
Projection — never stored, rebuilt by folding events with `target == id`.
```dsl
id:          Id
title:       @Field
description: @Field
start:       @Field         // Field.value is ISO datetime, "" == none
end:         @Field
column:      @Field         // Field.value is a @Board.columns name
board:       @Field         // Field.value is a @Board.name
assigned_to: @Field         // Field.value is a github login
report_to:   @Field
```

### Board
Projection, folded from events with `target == id`.
```dsl
id:      Id
name:    @Field             // unique across boards
columns: Text[]             // ordered, unique
```

## Operations

Each operation appends one `Event` (`~>`). No locks — concurrent writes are
allowed and reconciled at replay. Reads (`ViewBoard`, `ListItems`) fold the log.

### Board

- [ ] ViewBoard (b: @Board) -> (@Board + items: @Item[])
- [ ] ListBoards () -> (@Board[])
- [ ] CreateBoard (name: Text, columns: Text[]) -> (@Board)   ~> Event(op: CreateBoard)
- [ ] RenameBoard (b: @Board, name: Text) -> (@Board)         ~> Event(op: RenameBoard, field: name)
- [ ] DeleteBoard (b: @Board) -> ()                           ~> Event(op: DeleteBoard)

### Column

- [ ] CreateColumn (b: @Board, name: Text) -> (@Board)             ~> Event(op: CreateColumn, field: columns)
- [ ] RenameColumn (b: @Board, col: Text, name: Text) -> (@Board)  ~> Event(op: RenameColumn, field: columns)
- [ ] SwapColumn (b: @Board, col: Text, with: Text) -> (@Board)    ~> Event(op: SwapColumn, field: columns)
- [ ] DeleteColumn (b: @Board, col: Text) -> (@Board)              ~> Event(op: DeleteColumn, field: columns)

### Item

- [ ] ViewItem (i: @Item) -> (@Item)
- [ ] ListItems (filter: {key: Text}) -> (@Item[])           // see DSL.md filter keys
- [ ] CreateItem (b: @Board, col: Text, title: Text) -> (@Item)   ~> Event(op: CreateItem)
- [ ] EditItem (i: @Item, field: Text, value: Text) -> (@Item)    ~> Event(op: EditItem, field, parent)
- [ ] DeleteItem (i: @Item) -> ()                                 ~> Event(op: DeleteItem)
- [ ] MoveItemVertical (i: @Item, dir: Up | Down, over: @Item) -> ()   ~> Event(op: EditItem, field: order)
- [ ] MoveItemHorizontal (i: @Item, to: Text, push: @Item?) -> ()      ~> Event(op: EditItem, field: column)

### Conflicts

- [ ] ListConflicts (b: @Board?) -> (@Conflict[])           // divergences found at replay
- [ ] ResolveConflict (c: @Conflict, keep: @Event) -> ()    ~> Event(op: ResolveConflict, field: c.field, parent: [all c.events])

`ResolveConflict` appends an event whose `parent` names every conflicting event,
re-joining the lineage onto the chosen value (a merge commit) so the next replay
is unambiguous.

### Config

- [ ] ChangeKeybinding (action: Text, binding: Text) -> ()   ~> Event(op: ChangeKeybinding)
- [ ] ChangeSetting (key: Text, value: Text) -> ()           ~> Event(op: ChangeSetting)
