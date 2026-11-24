"""Normalize an existing WordPress WXR XML for Toggle import without scraping."""
from copy import deepcopy
from collections import Counter
from urllib.parse import urlparse, unquote
import re
from xml.etree import ElementTree as ET
from bs4 import BeautifulSoup
from combined import canonical

WP='http://wordpress.org/export/1.2/'
CONTENT='http://purl.org/rss/1.0/modules/content/'
NS={'wp':WP,'content':CONTENT}
for prefix,uri in [('wp',WP),('content',CONTENT),('excerpt','http://wordpress.org/export/1.2/excerpt/'),('dc','http://purl.org/dc/elements/1.1/'),('wfw','http://wellformedweb.org/CommentAPI/'),('atom','http://www.w3.org/2005/Atom')]: ET.register_namespace(prefix,uri)

def tag(local): return '{'+WP+'}'+local

def val(item, local): return (item.findtext(tag(local)) or '').strip()

def normalize_url(url):
    if not url: return url
    u=canonical(url.strip())
    return u

def clean_html(html,aliases):
    soup=BeautifulSoup(html or '', 'html.parser')
    for element in list(soup.find_all(['script','style'])): element.decompose()
    for element in soup.find_all(True):
        for attr in list(element.attrs):
            if attr in ('style','class','id','srcset','sizes','loading','decoding') or attr.startswith('data-') or attr.startswith('on'):
                del element.attrs[attr]
        for attr in ('src','poster'):
            if element.has_attr(attr):
                original=element[attr];element[attr]=aliases.get(normalize_url(original),normalize_url(original))
        if element.name=='a' and element.has_attr('href'):
            original=element['href']
            if original.startswith(('http://','https://')) and normalize_url(original) in aliases:
                element['href']=aliases[normalize_url(original)]
    return str(soup)

def format_wxr(data, max_posts=100, max_media=100):
    if len(data)>30*1024*1024: raise ValueError('XML is larger than the 30 MB upload limit.')
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper(): raise ValueError('XML DTD/entities are not supported.')
    try: root=ET.fromstring(data)
    except ET.ParseError as e: raise ValueError(f'Invalid XML: {e}') from e
    if root.tag!='rss': raise ValueError('Expected a WordPress WXR RSS XML export.')
    channel=root.find('channel')
    if channel is None: raise ValueError('RSS channel is missing.')
    if channel.find(tag('wxr_version')) is None: raise ValueError('Missing WordPress WXR version marker.')
    original_items=list(channel.findall('item'))
    posts=[i for i in original_items if val(i,'post_type')=='post']
    attachments=[i for i in original_items if val(i,'post_type')=='attachment']
    if not posts: raise ValueError('No blog post records found in the XML.')
    if len(posts)>max_posts or len(attachments)>max_media: raise ValueError(f'File contains {len(posts)} posts and {len(attachments)} attachments; limits are {max_posts} and {max_media}. Nothing was removed.')
    ids={}
    duplicates=[]; retained=[]; alias={}
    for item in attachments:
        url=item.findtext(tag('attachment_url')) or item.findtext('link') or ''
        key=normalize_url(url)
        if not key: retained.append(item);continue
        oldid=val(item,'post_id')
        if key in ids:
            if oldid: alias[oldid]=ids[key]
            duplicates.append(url)
            continue
        ids[key]=oldid
        retained.append(item)
        node=item.find(tag('attachment_url'))
        if node is not None: node.text=key
        # Preserve link and guid values; only attachment download URL is canonicalized.
    known={k:k for k in ids}
    for item in posts:
        body=item.find('{'+CONTENT+'}encoded')
        if body is not None: body.text=clean_html(body.text,known)
        for meta in item.findall(tag('postmeta')):
            if (meta.findtext(tag('meta_key')) or '').strip()=='_thumbnail_id':
                v=meta.find(tag('meta_value'))
                if v is not None and (v.text or '').strip() in alias: v.text=alias[v.text.strip()]
    for item in original_items: channel.remove(item)
    for item in original_items:
        if item in attachments and item not in retained: continue
        channel.append(item)
    out=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    ET.fromstring(out)
    return out,{'posts':len(posts),'attachments_before':len(attachments),'attachments_after':len(retained),'duplicate_media_removed':len(duplicates),'duplicates':duplicates[:30],'other_records':len(original_items)-len(posts)-len(attachments)}
