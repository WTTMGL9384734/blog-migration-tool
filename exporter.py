"""Public blog -> WordPress WXR 1.2 plus downloadable media archive."""
import datetime as dt
import email.utils
import hashlib
import io
import ipaddress
import json
import mimetypes
import re
import socket
import zipfile
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

UA = 'BlogMigrationTool/1.0 (public website content migration)'
NS = {'content': 'http://purl.org/rss/1.0/modules/content/', 'excerpt': 'http://wordpress.org/export/1.2/excerpt/', 'dc': 'http://purl.org/dc/elements/1.1/', 'wp': 'http://wordpress.org/export/1.2/'}
for k,v in NS.items(): ET.register_namespace(k,v)
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': UA})
MAX_BYTES = 12 * 1024 * 1024
MAX_TOTAL_MEDIA = 120 * 1024 * 1024
MAX_MEDIA_FILES = 300

class ExportError(Exception): pass

def checked_url(url):
    p = urlparse(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ExportError('Enter a public http:// or https:// URL.')
    host = p.hostname.lower()
    if host == 'localhost' or host.endswith(('.local','.internal','.localhost')):
        raise ExportError('Local or internal addresses are not supported.')
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                raise ExportError('Private or reserved network addresses are not supported.')
    except socket.gaierror as e:
        raise ExportError(f'Could not resolve website: {e}') from e
    return url

def fetch(url, binary=False, limit=MAX_BYTES):
    for _ in range(6):
        checked_url(url)
        try:
            r = SESSION.get(url, timeout=(8,25), allow_redirects=False, stream=True)
        except requests.RequestException as e:
            raise ExportError(f'Could not fetch {url}: {e}') from e
        if r.status_code in (301,302,303,307,308):
            loc=r.headers.get('Location')
            r.close()
            if not loc: raise ExportError('Redirect without Location header')
            url=urljoin(url,loc)
            continue
        try:
            r.raise_for_status()
            content=bytearray()
            for chunk in r.iter_content(65536):
                content.extend(chunk)
                if len(content)>limit: raise ExportError(f'Response exceeded {limit//1024//1024} MB limit: {url}')
            return bytes(content), r.headers.get('Content-Type',''),url
        except requests.HTTPError as e:
            raise ExportError(f'HTTP {r.status_code} for {url}') from e
        finally: r.close()
    raise ExportError('Too many redirects')

def text_html(value):
    return BeautifulSoup(value or '', 'html.parser').get_text(' ',strip=True)

def cleanup(html, base):
    soup=BeautifulSoup(html or '', 'html.parser')
    for tag in soup.select('script,style,form,nav,button,iframe,.sharedaddy,.jp-relatedposts,.comments-area,.post-navigation'):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr in ('class','id','style','srcset','sizes','loading','decoding','width','height') or attr.startswith('on'):
                del tag[attr]
        for attr in ('src','href','poster'):
            if tag.get(attr):
                val=tag[attr]
                if val.startswith(('javascript:','data:')): del tag[attr]
                else: tag[attr]=urljoin(base,val)
    return str(soup)

def date_from(raw, fallback=None):
    if raw:
        try: return dt.datetime.fromisoformat(raw.replace('Z','+00:00'))
        except ValueError: pass
    if fallback:
        m=re.search(r'/(20\d{2})/(\d{2})/(\d{2})/',urlparse(fallback).path)
        if m: return dt.datetime(*map(int,m.groups()))
    return dt.datetime(2000,1,1)

def wp_posts(base, max_posts, report):
    root=urlparse(base)
    origin=f'{root.scheme}://{root.netloc}'
    endpoint=origin+'/wp-json/wp/v2/posts?per_page=100&page=1&_embed=1'
    try:
        raw,ctype,_=fetch(endpoint)
        data=json.loads(raw)
        if not isinstance(data,list): return None
    except (ExportError,ValueError,json.JSONDecodeError): return None
    posts=[]; page=1
    while len(posts)<max_posts:
        if page>1:
            try:
                raw,_,_=fetch(origin+f'/wp-json/wp/v2/posts?per_page=100&page={page}&_embed=1')
                data=json.loads(raw)
            except (ExportError,ValueError,json.JSONDecodeError): break
            if not isinstance(data,list): break
        if not data: break
        for entry in data:
            if len(posts)>=max_posts: break
            link=entry.get('link','')
            if not link: continue
            embedded=entry.get('_embedded') or {}
            featured=''
            for item in embedded.get('wp:featuredmedia',[]):
                featured=item.get('source_url','')
                if featured: break
            posts.append(dict(title=text_html(entry.get('title',{}).get('rendered','')), url=link,
                slug=entry.get('slug') or urlparse(link).path.rstrip('/').split('/')[-1],
                date=date_from(entry.get('date'),link),
                body=cleanup(entry.get('content',{}).get('rendered',''),link),
                featured=featured, categories=[]))
        report(f'WordPress API: discovered {len(posts)} posts',min(.6,.1+len(posts)/max_posts*.5))
        if len(data)<100: break
        page+=1
    return posts

def html_posts(base,max_posts,report):
    origin=f'{urlparse(base).scheme}://{urlparse(base).netloc}'
    found=[]
    for page in range(1,11):
        candidates=[base] if page==1 else [base.rstrip('/')+f'/page/{page}/',base+('?' if '?' not in base else '&')+f'query-4-page={page}']
        links=[]
        for candidate in candidates:
            try: raw,_,actual=fetch(candidate)
            except ExportError: continue
            soup=BeautifulSoup(raw,'html.parser')
            for a in soup.select('article h2 a, article h3 a, .wp-block-post-title a, h2.entry-title a, .post-title a, a.wp-block-read-more'):
                if a.get('href'): links.append(urljoin(actual,a['href']))
            if not links:
                for a in soup.find_all('a',href=True):
                    if a.get_text(' ',strip=True).lower() in ('read more','continue reading'):
                        links.append(urljoin(actual,a['href']))
            if links: break
        fresh=[u for u in dict.fromkeys(links) if urlparse(u).netloc==urlparse(origin).netloc and u not in found]
        if not fresh: break
        found.extend(fresh)
        report(f'HTML archive: discovered {len(found)} post links', min(.4,.05+len(found)/max_posts*.3))
        if len(found)>=max_posts: break
    if not found: raise ExportError('No posts found. The site may require a custom selector, have blocked scraping, or use JavaScript-rendered content.')
    posts=[]
    for i,url in enumerate(found[:max_posts],1):
        try:
            raw,_,actual=fetch(url)
            soup=BeautifulSoup(raw,'html.parser')
            heading=soup.select_one('h1.entry-title, h1.wp-block-post-title, article h1, main h1, h1')
            meta=soup.select_one('meta[property="og:title"]')
            title=heading.get_text(' ',strip=True) if heading else (meta.get('content','') if meta else '')
            node=None
            for selector in ('.wp-block-post-content','.entry-content','.post-content','.article-content','article .content','main article','article'):
                n=soup.select_one(selector)
                if n and len(n.get_text(' ',strip=True))>=100: node=n;break
            if not title or node is None: raise ValueError('Missing title or full-content container')
            body=cleanup(str(node),actual)
            t=soup.select_one('time[datetime],meta[property="article:published_time"]')
            date=date_from((t.get('datetime') or t.get('content')) if t else '',actual)
            featured_meta=soup.select_one('meta[property="og:image"]')
            featured=urljoin(actual,featured_meta.get('content','')) if featured_meta else ''
            posts.append(dict(title=title,url=actual,slug=urlparse(actual).path.rstrip('/').split('/')[-1],date=date,body=body,featured=featured,categories=[]))
        except Exception as e:
            report(f'Skipped {url}: {e}')
        report(f'Extracted {i}/{min(len(found),max_posts)} HTML posts',.4+.2*i/min(len(found),max_posts))
    if not posts: raise ExportError('Found links, but could not extract complete posts.')
    return posts

def media_urls(posts):
    urls={}
    for p in posts:
        if p['featured']: urls[p['featured']]=None
        soup=BeautifulSoup(p['body'],'html.parser')
        for tag in soup.find_all(['img','source','video','audio','a']):
            key='href' if tag.name=='a' else 'src'
            val=tag.get(key,'')
            if not val or not val.startswith(('http://','https://')): continue
            if tag.name!='a' or urlparse(val).path.lower().endswith(('.jpg','.jpeg','.png','.gif','.webp','.svg','.pdf','.mp4','.mp3')):
                urls[val]=None
    return list(urls)[:MAX_MEDIA_FILES]

def add(parent,tag,value='',namespace=None,**attrs):
    name=f'{{{NS[namespace]}}}{tag}' if namespace else tag
    node=ET.SubElement(parent,name,attrs)
    node.text=str(value)
    return node

def wxr(posts,media,origin):
    rss=ET.Element('rss',{'version':'2.0'})
    ch=add(rss,'channel')
    add(ch,'title','Blog migration export');add(ch,'link',origin);add(ch,'description','Public blog export')
    add(ch,'language','en-US');add(ch,'wxr_version','1.2','wp');add(ch,'base_site_url',origin,'wp');add(ch,'base_blog_url',origin,'wp')
    ids={u:len(posts)+i for i,u in enumerate(media,1)}
    for i,p in enumerate(posts,1):
        item=add(ch,'item');add(item,'title',p['title']);add(item,'link',p['url'])
        add(item,'pubDate',email.utils.format_datetime(p['date']));add(item,'creator','migration','dc')
        add(item,'guid',p['url'],None,isPermaLink='false');add(item,'description')
        add(item,'encoded',p['body'],'content');add(item,'encoded','','excerpt')
        add(item,'post_id',i,'wp');add(item,'post_date',p['date'].strftime('%Y-%m-%d %H:%M:%S'),'wp')
        add(item,'post_date_gmt',p['date'].astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if p['date'].tzinfo else p['date'].strftime('%Y-%m-%d %H:%M:%S'),'wp')
        for k,v in [('post_name',p['slug']),('status','publish'),('post_type','post'),('post_parent','0'),('comment_status','closed'),('ping_status','closed'),('is_sticky','0')]:add(item,k,v,'wp')
        if p['featured'] in ids:
            meta=add(item,'postmeta','','wp');add(meta,'meta_key','_thumbnail_id','wp');add(meta,'meta_value',ids[p['featured']],'wp')
    for url,i in ids.items():
        item=add(ch,'item');name=urlparse(url).path.rsplit('/',1)[-1] or 'media'
        add(item,'title',name);add(item,'link',url);add(item,'guid',url,None,isPermaLink='false')
        add(item,'post_id',i,'wp');add(item,'post_type','attachment','wp');add(item,'status','inherit','wp')
        add(item,'post_name',re.sub('[^a-z0-9-]','-',name.lower()),'wp')
        add(item,'attachment_url',url,'wp');add(item,'post_mime_type',mimetypes.guess_type(name)[0] or 'application/octet-stream','wp')
    return ET.tostring(rss,encoding='utf-8',xml_declaration=True)

def export_site(url,max_posts=100,include_media=True,report=lambda *_:None):
    url=checked_url(url.strip())
    origin=f'{urlparse(url).scheme}://{urlparse(url).netloc}'
    report('Checking WordPress REST API',.02)
    posts=wp_posts(url,max_posts,report)
    method='WordPress REST API'
    if posts is None:
        method='HTML archive fallback'
        report('REST API unavailable; trying HTML archive',.05)
        posts=html_posts(url,max_posts,report)
    if not posts: raise ExportError('No public posts were returned.')
    warnings=[]
    for p in posts:
        if len(text_html(p['body']))<100: warnings.append(f"Short content: {p['title']} ({p['url']})")
    media=media_urls(posts)
    if len(media)==MAX_MEDIA_FILES: warnings.append('Media capped at 300 files.')
    xml=wxr(posts,media,origin)
    ET.fromstring(xml)
    report(f'Created XML with {len(posts)} posts and {len(media)} attachment records',.7)
    zipbuf=io.BytesIO(); downloaded=0; total=0
    slug=re.sub('[^a-z0-9-]','-',urlparse(origin).netloc.lower()).strip('-')
    xmlname=f'{slug}-blog.xml'
    with zipfile.ZipFile(zipbuf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr(xmlname,xml)
        if include_media:
            for i,asset in enumerate(media,1):
                if total>=MAX_TOTAL_MEDIA:
                    warnings.append('Stopped media downloads at 120 MB total.');break
                try:
                    data,ctype,_=fetch(asset,binary=True,limit=min(MAX_BYTES,MAX_TOTAL_MEDIA-total))
                    if 'text/html' in ctype.lower(): raise ExportError('Received HTML instead of media')
                    name=urlparse(asset).path.rsplit('/',1)[-1] or 'asset'
                    name=re.sub('[^a-zA-Z0-9._-]','_',name)
                    if '.' not in name:name+=(mimetypes.guess_extension(ctype.split(';')[0]) or '.bin')
                    name=hashlib.sha256(asset.encode()).hexdigest()[:10]+'-'+name
                    z.writestr('media/'+name,data)
                    total+=len(data);downloaded+=1
                except Exception as e:
                    warnings.append(f'Media download failed: {asset} ({e})')
                report(f'Downloading media {i}/{len(media)}',.7+.28*i/max(1,len(media)))
        z.writestr('media-url-map.json',json.dumps({'source_url':url,'media_urls':media},indent=2))
        z.writestr('README.txt',f'Export method: {method}\nPosts: {len(posts)}\nMedia URLs: {len(media)}\nMedia downloaded: {downloaded}\n\nWordPress XML references remote image URLs; image binaries are NOT embedded. Import while the source is online and enable Download and import file attachments. The media/ folder is an independent backup, not an automatic offline import. Verify destination images before shutting down the source.\n\nWarnings:\n'+'\n'.join(warnings))
    return {'zip':zipbuf.getvalue(),'xml':xml,'filename':f'{slug}-blog-export.zip','xml_filename':xmlname,'post_count':len(posts),'media_count':len(media),'downloaded_count':downloaded,'warnings':warnings}
