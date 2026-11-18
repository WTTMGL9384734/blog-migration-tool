"""Two-stage WXR export with optional manual Toggle verification."""
import datetime as dt
import email.utils
import io
import json
import mimetypes
import posixpath
import re
import zipfile
from urllib.parse import urlparse, unquote
from xml.etree import ElementTree as ET
from bs4 import BeautifulSoup
from exporter import (ExportError, checked_url, fetch, wp_posts, html_posts, media_urls,
                      add, NS, MAX_MEDIA_FILES, MAX_BYTES, MAX_TOTAL_MEDIA)

def discover(source, max_posts, report):
    source=checked_url(source.strip())
    posts=wp_posts(source,max_posts,report)
    method='WordPress REST API'
    if posts is None:
        method='HTML archive fallback'
        posts=html_posts(source,max_posts,report)
    if not posts: raise ExportError('No blog posts found.')
    media=media_urls(posts)
    if len(media)>=MAX_MEDIA_FILES:
        raise ExportError('Media limit reached (300). Reduce the post count to avoid incomplete migration.')
    return {'source':source,'posts':posts,'media':media,'method':method}

def filename_key(url):
    name=unquote(posixpath.basename(urlparse(url).path)).lower()
    # WordPress creates size variants such as image-300x200.jpg
    return re.sub(r'-\d+x\d+(?=\.[^.]+$)','',name)

def media_xml(bundle):
    origin=bundle['source']; rss=ET.Element('rss',{'version':'2.0'}); ch=add(rss,'channel')
    add(ch,'title','Media migration');add(ch,'link',origin)
    add(ch,'wxr_version','1.2','wp');add(ch,'base_site_url',origin,'wp');add(ch,'base_blog_url',origin,'wp')
    for i,url in enumerate(bundle['media'],100001):
        name=unquote(posixpath.basename(urlparse(url).path)) or f'asset-{i}'
        item=add(ch,'item');add(item,'title',name);add(item,'link',url)
        add(item,'guid',url,None,isPermaLink='false');add(item,'post_id',i,'wp')
        add(item,'post_date','2020-01-01 00:00:00','wp');add(item,'post_date_gmt','2020-01-01 00:00:00','wp')
        add(item,'post_type','attachment','wp');add(item,'status','inherit','wp')
        add(item,'post_name',re.sub('[^a-z0-9-]','-',name.lower()),'wp')
        add(item,'attachment_url',url,'wp')
        add(item,'post_mime_type',mimetypes.guess_type(name)[0] or 'application/octet-stream','wp')
    return ET.tostring(rss,encoding='utf-8',xml_declaration=True)

def backup_zip(bundle,report):
    buf=io.BytesIO();warnings=[];total=0;success=0
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('media.xml',media_xml(bundle))
        for i,url in enumerate(bundle['media'],1):
            if total>=MAX_TOTAL_MEDIA:
                warnings.append('Media backup exceeded 120 MB; some files omitted.');break
            try:
                raw,ctype,_=fetch(url,binary=True,limit=min(MAX_BYTES,MAX_TOTAL_MEDIA-total))
                if 'text/html' in ctype.lower(): raise ExportError('Returned HTML')
                name=re.sub('[^a-zA-Z0-9._-]','_',unquote(posixpath.basename(urlparse(url).path))) or f'asset-{i}'
                z.writestr(f'media/{i:04d}-{name}',raw);total+=len(raw);success+=1
            except ExportError as exc: warnings.append(f'{url}: {exc}')
            report(f'Downloading media {i}/{len(bundle["media"])}',i/max(len(bundle['media']),1))
        z.writestr('manifest.json',json.dumps({'source':bundle['source'],'media_urls':bundle['media']},indent=2))
        z.writestr('README.txt','Media XML uses source image URLs for WordPress to download. Import while source site is online. The media directory is an offline backup, not an import payload. Verify destination media before importing posts.\n')
    return buf.getvalue(),success,warnings

def destination_media(destination,report):
    destination=checked_url(destination.strip())
    p=urlparse(destination);origin=f'{p.scheme}://{p.netloc}'
    results=[]
    for page in range(1,51):
        endpoint=f'{origin}/wp-json/wp/v2/media?per_page=100&page={page}&_fields=id,source_url,slug,media_details'
        try:
            raw,_,_=fetch(endpoint,limit=3*1024*1024)
            entries=json.loads(raw)
        except (ExportError,ValueError) as exc:
            if page==1: raise ExportError('Cannot read public destination Media API. Verification requires a publicly readable /wp-json/wp/v2/media endpoint. '+str(exc)) from exc
            break
        if not isinstance(entries,list): raise ExportError('Destination did not return a WordPress media list.')
        results.extend(entries)
        report(f'Checked {len(results)} destination media items',min(0.95,page/50))
        if len(entries)<100:break
    else: raise ExportError('Destination media exceeds 5,000 records; cannot verify safely.')
    return results

def match_media(bundle, entries):
    index={}
    for entry in entries:
        url=entry.get('source_url','')
        if url: index.setdefault(filename_key(url),[]).append(entry)
        details=entry.get('media_details') or {}
        for variant in (details.get('sizes') or {}).values():
            if isinstance(variant,dict) and variant.get('source_url'):
                index.setdefault(filename_key(variant['source_url']),[]).append(entry)
    matched={};missing=[];ambiguous=[]
    for source in bundle['media']:
        options={e['id']:e for e in index.get(filename_key(source),[]) if e.get('id') and e.get('source_url')}
        if len(options)==1: matched[source]=next(iter(options.values()))
        elif len(options)>1: ambiguous.append(source)
        else: missing.append(source)
    return matched,missing,ambiguous

def rewrite_html(body,mapping):
    soup=BeautifulSoup(body,'html.parser')
    for tag in soup.find_all(True):
        for attr in ('src','href','poster'):
            val=tag.get(attr)
            if val in mapping: tag[attr]=mapping[val]['source_url']
    return str(soup)

def posts_xml(bundle,mapping,destination):
    rss=ET.Element('rss',{'version':'2.0'});ch=add(rss,'channel')
    add(ch,'title','Blog posts migration');add(ch,'link',destination)
    add(ch,'wxr_version','1.2','wp');add(ch,'base_site_url',destination,'wp');add(ch,'base_blog_url',destination,'wp')
    for i,p in enumerate(bundle['posts'],1):
        item=add(ch,'item');add(item,'title',p['title']);add(item,'link',p['url'])
        add(item,'pubDate',email.utils.format_datetime(p['date']));add(item,'creator','migration','dc')
        add(item,'guid',p['url'],None,isPermaLink='false');add(item,'description')
        add(item,'encoded',rewrite_html(p['body'],mapping),'content');add(item,'encoded','','excerpt')
        add(item,'post_id',i,'wp');add(item,'post_date',p['date'].strftime('%Y-%m-%d %H:%M:%S'),'wp')
        add(item,'post_date_gmt',p['date'].astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if p['date'].tzinfo else p['date'].strftime('%Y-%m-%d %H:%M:%S'),'wp')
        for k,v in [('post_name',p['slug']),('status','publish'),('post_type','post'),('post_parent','0'),('comment_status','closed'),('ping_status','closed'),('is_sticky','0')]: add(item,k,v,'wp')
        if p['featured']:
            if p['featured'] in mapping and mapping[p['featured']].get('id'):
                meta=add(item,'postmeta','','wp');add(meta,'meta_key','_thumbnail_id','wp')
                add(meta,'meta_value',mapping[p['featured']]['id'],'wp')
    return ET.tostring(rss,encoding='utf-8',xml_declaration=True)
