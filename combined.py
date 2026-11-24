"""One-file WXR 1.2 blog export: posts and attachment records together."""
import datetime as dt
import email.utils
import mimetypes
import posixpath
import re
from urllib.parse import urlparse, unquote, urlunparse
from xml.etree import ElementTree as ET
from bs4 import BeautifulSoup
from exporter import ExportError, checked_url, wp_posts, html_posts, media_urls, add

def canonical(url):
    """Normalize image proxy and resize URLs while retaining original media location."""
    p = urlparse(url)
    path = p.path
    if p.hostname and p.hostname.lower().endswith('.wp.com') and p.hostname.lower().startswith('i'):
        # WordPress image CDN: /source-domain/wp-content/uploads/... -> source domain
        parts = path.lstrip('/').split('/', 1)
        if len(parts) == 2 and '.' in parts[0] and parts[1].startswith('wp-content/uploads/'):
            return urlunparse(('https', parts[0], '/' + parts[1], '', '', ''))
    if p.query and (p.hostname or '').lower().endswith('.wp.com'):
        return urlunparse((p.scheme,p.netloc,path,p.params,'',''))
    return urlunparse((p.scheme,p.netloc,path,p.params,'','')) if p.query and any(k in p.query for k in ('resize=','fit=','ssl=')) else url

def collect(source, maximum, report=lambda *_: None):
    source=checked_url(source.strip())
    posts=wp_posts(source,maximum,report)
    method='WordPress REST API'
    if posts is None:
        method='HTML archive fallback'
        posts=html_posts(source,maximum,report)
    if not posts: raise ExportError('No posts found.')
    raw=media_urls(posts, limit=None)
    media=[]; seen=set(); aliases={}
    for url in raw:
        key=canonical(url)
        aliases[url]=key
        if key not in seen:
            media.append(key);seen.add(key)
    if len(media)>maximum: raise ExportError(f'Media discovery found {len(media)} unique files, exceeding the selected limit of {maximum}. Increase the limit or scan a smaller source.')
    return {'source':source,'posts':posts,'media':media,'aliases':aliases,'method':method}

def create_xml(bundle):
    posts=bundle['posts']; media=bundle['media']; origin=bundle['source']
    rss=ET.Element('rss',{'version':'2.0'});ch=add(rss,'channel')
    for tag,val in [('title','Complete blog migration'),('link',origin),('description','Posts and attachments in one WXR import'),('language','en-US')]: add(ch,tag,val)
    for tag in ('wxr_version','base_site_url','base_blog_url'):
        add(ch,tag,'1.2' if tag=='wxr_version' else origin,'wp')
    ids={u:len(posts)+i for i,u in enumerate(media,1)}
    aliases=bundle['aliases']
    for i,p in enumerate(posts,1):
        item=add(ch,'item');add(item,'title',p['title']);add(item,'link',p['url'])
        add(item,'pubDate',email.utils.format_datetime(p['date']));add(item,'creator','migration','dc')
        add(item,'guid',p['url'],None,isPermaLink='false');add(item,'description')
        # Keep full content; normalize known CDN image references to attachment URLs.
        soup=BeautifulSoup(p['body'],'html.parser')
        for node in soup.find_all(True):
            for attr in ('src','poster','href'):
                val=node.get(attr)
                if val in aliases: node[attr]=aliases[val]
        add(item,'encoded',str(soup),'content');add(item,'encoded','','excerpt')
        add(item,'post_id',i,'wp');add(item,'post_date',p['date'].strftime('%Y-%m-%d %H:%M:%S'),'wp')
        add(item,'post_date_gmt',p['date'].astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if p['date'].tzinfo else p['date'].strftime('%Y-%m-%d %H:%M:%S'),'wp')
        for k,v in [('post_name',p['slug']),('status','publish'),('post_type','post'),('post_parent','0'),('comment_status','closed'),('ping_status','closed'),('is_sticky','0')]: add(item,k,v,'wp')
        featured=aliases.get(p.get('featured'),canonical(p['featured']) if p.get('featured') else None)
        if featured in ids:
            meta=add(item,'postmeta','','wp');add(meta,'meta_key','_thumbnail_id','wp');add(meta,'meta_value',ids[featured],'wp')
    for url,i in ids.items():
        name=unquote(posixpath.basename(urlparse(url).path)) or f'asset-{i}'
        item=add(ch,'item');add(item,'title',name);add(item,'link',url)
        add(item,'guid',url,None,isPermaLink='false');add(item,'post_id',i,'wp')
        add(item,'post_date','2020-01-01 00:00:00','wp');add(item,'post_date_gmt','2020-01-01 00:00:00','wp')
        add(item,'post_type','attachment','wp');add(item,'status','inherit','wp');add(item,'post_parent','0','wp')
        add(item,'post_name',re.sub(r'[^a-z0-9-]','-',posixpath.splitext(name)[0].lower()),'wp')
        add(item,'attachment_url',url,'wp');add(item,'post_mime_type',mimetypes.guess_type(name)[0] or 'application/octet-stream','wp')
    result=ET.tostring(rss,encoding='utf-8',xml_declaration=True)
    ET.fromstring(result)
    return result
