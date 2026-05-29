# AVEVA Connect Data Services MCP Server

A minimal [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server
that connects an AI assistant to AVEVA Connect Data Services — specifically the
Sequential Data Store (SDS).

This project is a training reference for AVEVA system integrators. It is
intentionally small. Every design decision is explained so you can read the
code, understand why it is structured the way it is, and apply the same
patterns to your own integrations.

---

## What this demonstrates

AI assistants do not natively know how to query your historian. MCP is the
bridge. It lets you expose any data source — a REST API, a database, a COM
object, anything — as a set of callable tools that an AI can discover and use
during a conversation.

Building those tools is not a black box. This project shows the full picture:

- How to wrap a real industrial REST API as MCP tools
- How OAuth 2.0 authentication works in this context
- How to write tool descriptions that give the AI enough context to use them
  correctly
- How to run and test the server without an AVEVA account

---

## The scenario (demo mode)

When no credentials are configured the server runs in **demo mode**, returning
pre-generated data for a fictional utility: **Aveva Water Authority (AWA)**.

The AWA namespace contains six streams:

| Stream ID | Description | Units |
|---|---|---|
| `FIT-101.PV` | Influent flow rate — diurnal pattern | gpm |
| `AIT-301.PV` | Aeration basin dissolved oxygen | mg/L |
| `PDT-401.PV` | Filter 1 head loss — drifting toward backwash threshold | ft |
| `AIT-501.PV` | Effluent turbidity — spike ~6 hours ago, now recovered | NTU |
| `FIC-601.PV` | Chlorine dosing rate — correlated with influent flow | lb/day |
| `P-201.RunStatus` | Transfer pump 201 run/stop status | Boolean |

The data is designed to give the AI something meaningful to say. The filter
head loss drift, the turbidity exceedance, the pump trip, and the dosing
correlation are all present and discoverable through normal tool use.

Demo data is deterministic — generated from a fixed seed — so the AI sees the
same values every time, making training sessions reproducible.

---

## Quick start (demo mode — no account required)

**1. Clone and set up a virtual environment**

```bash
git clone https://github.com/your-org/connect-data-services-mcp.git
cd connect-data-services-mcp
python -m venv .venv
```

Windows:
```powershell
.venv\Scripts\Activate.ps1
```

macOS/Linux:
```bash
source .venv/bin/activate
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
```

**3. Run the server**

```bash
python server.py
```

You should see:
```
connect-data-services MCP server starting (DEMO — Aveva Water Authority mock data)
```

The server is now running over stdio, ready for an MCP client to connect.

---

## Connecting to Claude Desktop

Add this block to your Claude Desktop configuration file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "connect-data-services": {
      "command": "python",
      "args": ["C:/path/to/connect-data-services-mcp/server.py"]
    }
  }
}
```

Replace the path with the actual location of `server.py`. Restart Claude
Desktop after saving.

The four tools (`list_namespaces`, `list_streams`, `get_stream_metadata`,
`get_values`) will appear in Claude's tool list automatically.

---

## Live mode (real AVEVA Connect tenant)

**1. Get credentials from the Connect portal**

In the portal (`connect.aveva.com`):
- Your **Tenant ID** is shown in Account Settings (top-right corner)
- **Client ID** and **Secret** are created under Security → Clients

**2. Configure the environment**

Copy `.env.example` to `.env` and fill in your values:

```
CONNECT_TENANT_ID=your-tenant-id
CONNECT_CLIENT_ID=your-client-id
CONNECT_CLIENT_SECRET=your-client-secret
CONNECT_REGION=uswe
```

The region slug matches the subdomain of your Connect portal URL. Common
values: `uswe` (US West), `euno` (EU North), `apso` (Asia Pacific South).

**3. Run the server**

```bash
python server.py
```

You should see:
```
connect-data-services MCP server starting (LIVE — tenant your-tenant-id)
```

All four tools now make real calls to your tenant's SDS namespace.

---

## The four tools

### `list_namespaces`

Returns the namespaces in your tenant. A namespace is a partitioned
time-series store — typically one per site or operational area. The `Id`
field from the response is used in every subsequent call.

### `list_streams`

Lists streams in a namespace. A stream is the SDS equivalent of a PI tag or
historian point — a time-ordered sequence of values for a single measurement.
The optional `query` parameter filters by Id, Name, Description, or Tags.

### `get_stream_metadata`

Returns the full metadata record for one stream: its type, description,
engineering units, operating limits, and interpolation mode. Call this before
querying values so the AI has the context to interpret what it gets back.

The `InterpolationMode` field is worth understanding:
- `Continuous` — values between stored points are linearly interpolated
  (analog sensors, flow rates, temperatures)
- `StepwiseContinuousLeading` — the value holds until the next stored point
  (discrete signals like pump run/stop status)

### `get_values`

Queries values from a stream between a start and end timestamp (ISO 8601 UTC).
Returns up to `count` values, evenly sampled across the window. The default
count of 100 is appropriate for trend analysis; increase it for finer
resolution over a short event window.

---

## Design decisions

**Why four tools and not one?**

Each tool does one thing. The AI builds up context incrementally — first learn
what namespaces exist, then what streams they contain, then what a specific
stream means, then fetch its values. This mirrors how a human analyst would
approach an unfamiliar historian. It also gives the AI natural checkpoints to
confirm it is querying the right thing before consuming data.

**Why stdio transport?**

The MCP specification supports both stdio (subprocess) and HTTP transports.
Stdio is simpler to distribute: one command, no port to configure, no
firewall rules. For a server that runs locally alongside Claude Desktop,
stdio is the right default.

**Why auto-detect demo vs live mode?**

Removing friction is important for adoption. An SI who clones this repo and
runs `python server.py` gets a working server immediately. When they are ready
to connect real data, they add a `.env` file and nothing else changes.

**Why are descriptions long?**

Tool descriptions are the AI's primary source of context. A description that
says "returns flow values" tells the AI what the tool returns but not what
flow means, what units it is in, or what a normal value looks like. Richer
descriptions reduce hallucination and lead to better questions from the AI.
This is more important for smaller models that have less world knowledge to
draw on.

**Why is the mock data deterministic?**

Training and demos require reproducibility. If the data changed every time
you ran the server, you could not build a consistent narrative around it or
use screenshots from one session in a slide deck shown in the next. Seeding
random from the stream ID gives each stream its own independent history while
keeping results stable across restarts.

**Why does the mock data tell a story?**

Data with correlations and anomalies gives the AI something to reason about.
A filter trending toward its backwash threshold, a past turbidity exceedance
that coincided with a pump trip, dosing that tracks influent flow — these
are the kinds of patterns an operator cares about, and they are the patterns
that demonstrate the value of putting an AI over your historian.

---

## Project structure

```
connect-data-services-mcp/
├── server.py       Main file: FastMCP app, tool definitions, OAuth client
├── mock_data.py    Demo data: stream definitions and time-series generation
├── requirements.txt
├── .env.example    Credential template — copy to .env, never commit .env
└── .gitignore
```

---

## What comes next

This server covers read-only access to the SDS API. The same pattern extends
naturally to:

- **Event frames** — query operational events and correlate them with process
  data
- **Assets** — navigate the asset hierarchy to find streams by equipment
  rather than tag name
- **Annotations** — attach AI-generated observations back to the historian as
  a record
- **Write tools** — with appropriate authorization, update setpoints or
  acknowledge alarms

Each of these is a new tool following the same structure you see in
`server.py`. The pattern does not change; the scope expands.

---

## Resources

- [AVEVA Connect portal](https://connect.aveva.com)
- [SDS REST API reference](https://docs.had.aveva.com) — Sequential Data Store section
- [MCP specification](https://modelcontextprotocol.io)
- [FastMCP documentation](https://github.com/jlowin/fastmcp)
- [Claude Desktop MCP setup](https://docs.anthropic.com/claude/docs/mcp)
