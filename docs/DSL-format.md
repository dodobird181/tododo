# DSL Format

A plain Github-flavored markdown notation for describing a domain: its
**dataclasses** (data shapes) and its **domain operations** (commands and the
events they emit in an event-driven architecture). It renders as ordinary
markdown, yet is regular enough to parse into code.

A `.dsl.md` file has two halves: **Types** (dataclasses) and **Operations**
(events), cross-linked with `@` references.

## Types (dataclasses)

A dataclass is a `### TypeName` heading followed by one fenced ` ```dsl ` block
of `field: Type = default  // note` lines.

    ### User
    ```dsl
    id:       Id                // uuid hex, generated
    name:     Text
    email:    Text
    role:     Admin | Member    // enum-of
    manager:  @User?            // optional self-reference
    ```

### Type vocabulary

| Token            | Meaning                                          |
|------------------|--------------------------------------------------|
| `Text` `Int` `Bool` `Date` `Id` | scalar primitives                 |
| `@Type`          | reference to another dataclass in this file      |
| `@Type.field`    | reference to a specific field (foreign key)      |
| `T?`             | optional / nullable                              |
| `T[]`            | list of T                                        |
| `{K: V}`         | map                                              |
| `A \| B`         | union / enum-of                                  |
| `= x`            | default value                                    |
| `// ...`         | inline note (non-semantic)                       |

`@`-references make the file a **graph** of types, not just a list of structs —
they are the foreign-key edges between them.

## Operations (events)

Operations live under an **aggregate heading** (`### TypeName`) — the type they
act on. Each operation is one line:

    - [ ] OpName (in1: T, in2: T) -> (Out)   ~> EventEmitted(payload)

Read as: command `OpName` takes typed inputs, transforms to `Out`, and `~>`
**emits** the domain event(s) other parts of the system subscribe to. Both the
`-> (Out)` return and the `~> Event` are optional — a fire-and-forget command
has neither, a pure query has only the return.

### Operation arrows

| Arrow         | Role                                                     |
|---------------|----------------------------------------------------------|
| `->`          | synchronous transform: `(input) -> (output / return)`    |
| `~>`          | **emits event(s)** — the event-driven edge               |
| `+`           | compose several values into one payload / output         |
| `[ ]` / `[x]` | implemented yet?                                         |

Event payloads use the same type vocabulary as fields, so `~> UserInvited(@User,
by: @User)` is fully typed.

## Events

Events are the backbone of the architecture, so give each one a typed payload in
its own section — same block syntax as a dataclass — and subscribers have a
contract to read.

    ## Events

    ### UserInvited
    ```dsl
    user:    @User
    by:      @User
    at:      Date
    ```

## Full example

    # Example Domain

    ## Types

    ### Team
    ```dsl
    id:   Id
    name: Text
    ```

    ### User
    ```dsl
    id:    Id
    name:  Text
    team:  @Team.id
    role:  Admin | Member
    ```

    ## Operations

    ### User

    - [ ] InviteUser (name: Text, into: @Team) -> (@User)    ~> UserInvited(@User)
    - [ ] EditUser (u: @User, patch: {field: Text}) -> (@User)   ~> UserEdited(u, patch)
    - [ ] MoveUser (u: @User, to: @Team) -> ()              ~> UserMoved(u, to)
    - [ ] RemoveUser (u: @User) -> ()                       ~> UserRemoved(id: Id)
    - [ ] ListUsers (team: @Team?) -> (@User[])

    ## Events

    ### UserMoved
    ```dsl
    user: @User
    from: @Team.id
    to:   @Team.id
    ```
