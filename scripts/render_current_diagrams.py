from pathlib import Path
from html import escape
import re, zipfile
import cairosvg

OUT=Path(__file__).resolve().parents[1]/'docs/diagrams'
def render(name,title,subtitle,nodes,edges,w=1400,h=1200):
    s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">', '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#8576b7"/></marker></defs>',f'<rect width="{w}" height="{h}" fill="#faf9fe"/>', f'<text x="50" y="55" font-family="DejaVu Sans" font-size="30" font-weight="bold" fill="#211938">{escape(title)}</text>',f'<text x="50" y="90" font-family="DejaVu Sans" font-size="16" fill="#686078">{escape(subtitle)}</text>']
    for a,b,label in edges:
        x,y,bw,bh,*_=nodes[a]; X,Y,BW,BH,*_=nodes[b]
        if Y>=y+bh:
            sx,sy=x+bw/2,y+bh; ex,ey=X+BW/2,Y
            mid=(sy+ey)/2
            path=f'M {sx} {sy} V {mid} H {ex} V {ey}'
            lx,ly=(sx+ex)/2,mid-8
        elif Y+BH <= y:
            sx,sy=x+bw/2,y; ex,ey=X+BW/2,Y+BH
            mid=(sy+ey)/2
            path=f'M {sx} {sy} V {mid} H {ex} V {ey}'
            lx,ly=(sx+ex)/2,mid-8
        else:
            sx,sy=(x+bw if X>x else x),y+bh/2; ex,ey=(X if X>x else X+BW),Y+BH/2
            mid=(sx+ex)/2
            path=f'M {sx} {sy} H {mid} V {ey} H {ex}'
            lx,ly=mid+5,(sy+ey)/2-8
        s.append(f'<path d="{path}" fill="none" stroke="#8576b7" stroke-width="2" marker-end="url(#arrow)"/>')
        if label:s.append(f'<text x="{lx}" y="{ly}" text-anchor="middle" font-family="DejaVu Sans" font-size="13" fill="#605280">{escape(label)}</text>')
    for x,y,bw,bh,t,lines in nodes.values():
        s += [f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="12" fill="white" stroke="#b9a5f2" stroke-width="1.5"/>',f'<rect x="{x}" y="{y}" width="{bw}" height="42" rx="12" fill="#ede6ff"/>',f'<text x="{x+18}" y="{y+28}" font-family="DejaVu Sans" font-size="18" font-weight="bold" fill="#43218c">{escape(t)}</text>']
        for i,line in enumerate(lines):s.append(f'<text x="{x+18}" y="{y+67+i*23}" font-family="DejaVu Sans" font-size="15" fill="#393246">{escape(line)}</text>')
    s.append(f'<text x="50" y="{h-24}" font-family="DejaVu Sans" font-size="13" fill="#81778e">CashLens · Independent prototype · Proposed architecture · September 2026</text></svg>')
    svg=''.join(s); (OUT/f'{name}.svg').write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(),write_to=str(OUT/f'{name}.png'),scale=1.5)


render('08-current-system','CashLens: current system architecture','Uploads and synthetic fixtures share financial calculations and AI services.',{
'web':(60,160,540,145,'Next.js on Vercel',['Demo selection or private statement upload','Review, dashboard, evidence and scenarios']),
'api':(800,160,540,145,'FastAPI on Vercel',['Validate identity, membership and quotas','Ingestion, deterministic tools and AI']),
'auth':(60,460,540,140,'Supabase Auth and private Storage',['Authenticated ownership for real uploads','Scoped file access and expiration cleanup']),
'db':(800,460,540,140,'Supabase Postgres',['Demo fixtures and private business records','Staging, snapshots, experiments and traces']),
'ai':(800,780,540,145,'Server-side model adapter',['Direct OpenAI for real statements initially','Optional OpenRouter: synthetic evaluations']),
'ops':(60,780,540,145,'Processing and cleanup lifecycle',['Persisted jobs, bounded steps and retries','Verify timeouts and scheduled cleanup'])},[('web','api','API'),('web','auth','sign in / upload'),('api','db','scoped queries'),('api','ai','model calls'),('auth','ops','file lifecycle')],h=1050)
render('09-statement-upload','CashLens: statement upload workflow','CSV and supported text PDFs; review extracted information before analysis.',{
'up':(60,150,540,140,'1. Authenticate and upload',['Private business and account scope','Validate type, size, pages and quota']),
'ex':(800,150,540,140,'2. Extract draft transactions',['CSV mapping or PDF text extraction','Bounded AI assistance with source evidence']),
'check':(800,450,540,155,'3. Validate and reconcile',['Dates, signs, currency and statement period','Compare totals where available','Flag missing or uncertain information']),
'review':(60,450,540,155,'4. Owner review',['Correct rows and confirm account','Resolve potential duplicates','Unsupported files return a clear explanation']),
'commit':(60,780,540,140,'5. Confirm import atomically',['Check reviewed revision and idempotency','Preserve provenance; no partial publication']),
'analysis':(800,780,540,140,'6. Run the shared analysis',['Fresh classifications, metrics and briefs','Dated balances and labeled projections'])},[('up','ex',''),('ex','check',''),('check','review',''),('review','commit',''),('commit','analysis','')],h=1050)
render('10-private-data-model','CashLens: private import database relationships','PK = primary key; FK = foreign key. Arrows are parent to zero-or-many children.',{
'user':(60,140,540,125,'auth.users',['PK id','Authenticated owner identity']),
'member':(800,140,540,125,'business_memberships',['PK / FK business_id + user_id','Membership authorizes private business access']),
'biz':(800,410,540,125,'businesses',['PK id; data_mode = demo or private','Business scope enforced on linked records']),
'imp':(60,410,540,155,'document_imports',['PK id; FK business_id, uploaded_by','Object path, hash, format, draft_revision','Status, confirmation and expiration']),
'row':(60,760,540,155,'import_rows',['PK id; FK import_id','Source page / row; extracted and reviewed fields','Validation flags; unique import / row index']),
'acct':(800,760,540,155,'accounts',['PK id; FK business_id','Source type; optional connection_id','Account identity confirmed by owner']),
'src':(60,1090,540,155,'transaction_sources',['PK id; FK transaction_id','FK import_row_id OR raw_event_id','Multiple sources may support one transaction']),
'tx':(800,1090,540,155,'transactions',['PK id; FK account_id, business_id','Signed minor units, date, source, revision','Provider external_id nullable for uploads'])},[('user','member',''),('biz','member',''),('biz','imp',''),('imp','row',''),('biz','acct',''),('row','src',''),('acct','tx',''),('tx','src','')],h=1360)
render('11-import-jobs-and-ai','CashLens: import processing and AI relationships','Optional relationships and lifecycle details supplement the private import diagram.',{
'imp':(60,140,540,150,'document_imports',['PK id; FK business_id','Revisioned draft, confirmed or failed status','Linked private file with expiration timestamp']),
'jobs':(800,140,540,150,'import_jobs',['PK id; FK import_id','Lease expiry, attempts, idempotency key','Persist progress; bounded processing steps']),
'ws':(60,450,540,155,'workspaces',['PK id; FK business_id','Exactly one: visitor_id or user_id','Anonymous demos; authenticated private data']),
'ai':(800,450,540,155,'ai_runs',['PK id; FK business_id','Optional import_id and workspace_id','Model, prompt version, validation and usage']),
'snap':(60,800,540,155,'financial_snapshots',['PK id; FK workspace_id','Input revisions, as_of, calculation version','Insights and scenarios reference this snapshot']),
'calls':(800,800,540,155,'tool_calls',['PK id; FK ai_run_id','Validated arguments and scoped results','Private payloads excluded from public viewer'])},[('imp','jobs','1 : many'),('imp','ai','optional import context'),('ws','ai','optional workspace context'),('ws','snap','1 : many'),('ai','calls','1 : many')],h=1080)
