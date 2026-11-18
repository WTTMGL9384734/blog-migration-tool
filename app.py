import streamlit as st
from exporter import ExportError
from staged import discover, media_xml, backup_zip, destination_media, match_media, posts_xml

st.set_page_config(page_title='Blog Migration Tool',page_icon='📦')
st.title('Blog Migration Tool')
st.caption('Media first, verify in Toggle, then export posts. WordPress-compatible sites only.')
st.info('Media XML does not embed image files. Import it while the original site is online and select “Download and import file attachments.”')

def report(message,fraction=None):
    st.session_state['status']=message

with st.form('source'):
    source=st.text_input('Existing blog URL',placeholder='https://example.com/blog/')
    maximum=st.number_input('Maximum posts',1,300,100)
    start=st.form_submit_button('1. Scan blog and prepare media',type='primary')
if start:
    for k in ('bundle','media_zip','verified','destination','mapping'): st.session_state.pop(k,None)
    try:
        with st.spinner('Discovering posts and images...'):
            bundle=discover(source,int(maximum),report)
        st.session_state.bundle=bundle
    except (ExportError,ValueError) as exc:st.error(str(exc))

if 'bundle' in st.session_state:
    bundle=st.session_state.bundle
    st.subheader('Step 1: Import media')
    st.write(f"Found **{len(bundle['posts'])} posts** and **{len(bundle['media'])} image/media URLs** using {bundle['method']}.")
    st.download_button('Download Media XML',media_xml(bundle),file_name='media.xml',mime='application/xml')
    if st.button('Download independent media backup ZIP'):
        try:
            with st.spinner('Downloading original files...'):
                z,count,warnings=backup_zip(bundle,report)
            st.session_state.media_zip=z
            st.session_state.backup_warnings=warnings
            st.success(f'Backed up {count} of {len(bundle["media"])} media files.')
        except ExportError as exc:st.error(str(exc))
    if 'media_zip' in st.session_state:
        st.download_button('Save media backup ZIP',st.session_state.media_zip,'media-backup.zip','application/zip')
        for warning in st.session_state.get('backup_warnings',[]):st.warning(warning)
    st.caption('Import Media XML into Toggle first. The WordPress importer must download the files from the original site. Keep the backup ZIP.')
    st.subheader('Step 2: Verify Toggle media')
    with st.form('verify'):
        confirmed=st.checkbox('I imported the Media XML into Toggle and enabled attachment downloads.')
        destination=st.text_input('Toggle website URL',placeholder='https://client.brightervisionpreview.com/')
        verify=st.form_submit_button('Verify media import')
    if verify:
        st.session_state.pop('verified',None)
        if not confirmed:st.error('Confirm the media import first.')
        else:
            try:
                with st.spinner('Checking public WordPress Media API...'):
                    entries=destination_media(destination,report)
                    mapping,missing,ambiguous=match_media(bundle,entries)
                st.write(f'Matched **{len(mapping)}/{len(bundle["media"])}** media URLs.')
                if missing:st.error(f'{len(missing)} missing: '+', '.join(missing[:8]))
                if ambiguous:st.error(f'{len(ambiguous)} ambiguous filenames: '+', '.join(ambiguous[:8]))
                if not missing and not ambiguous:
                    st.session_state.verified=True
                    st.session_state.mapping=mapping
                    st.session_state.destination=destination
                    st.success('All expected media filenames matched unique destination attachments. Check the images visually before import.')
            except (ExportError,ValueError) as exc:st.error(str(exc))
    st.subheader('Step 3: Export posts')
    if st.session_state.get('verified'):
        st.warning('Featured-image IDs use existing Toggle Media Library IDs. Verify one test post first: some WordPress importers do not preserve direct _thumbnail_id metadata.')
        xml=posts_xml(bundle,st.session_state.mapping,st.session_state.destination)
        st.download_button('Download Posts XML',xml,'posts.xml','application/xml',type='primary')
    else:st.caption('Locked until all media is verified.')

st.divider()
st.caption('Prototype: public WordPress Media API is required for verification. Filename matching cannot prove image binary identity. No credentials are collected or stored.')
