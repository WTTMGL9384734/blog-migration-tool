import csv
import io
import streamlit as st
from exporter import ExportError, checked_url
from staged import discover, media_xml, backup_zip, posts_xml, discover_public_media, match_public_media

st.set_page_config(page_title='Blog Migration Tool', page_icon='📦')
st.title('Blog Migration Tool')
st.caption('Media-first migration for Toggle. No WordPress API is required on the destination.')
st.info('The Media XML references original URLs; import while the source site is live and enable downloading attachments in Toggle.')

def progress(message, fraction=None):
    st.session_state['status'] = message

with st.form('scan'):
    source = st.text_input('Existing blog URL', placeholder='https://example.com/blog/')
    maximum = st.number_input('Maximum posts', min_value=1, max_value=300, value=100)
    scan = st.form_submit_button('1. Scan blog and prepare media', type='primary')
if scan:
    for key in ('bundle', 'media_zip', 'verified', 'destination', 'mapping', 'verification_mode', 'discovery_report', 'backup_warnings'):
        st.session_state.pop(key, None)
    try:
        with st.spinner('Discovering posts and media...'):
            st.session_state.bundle = discover(source, int(maximum), progress)
    except (ExportError, ValueError) as exc:
        st.error(str(exc))

if 'bundle' in st.session_state:
    bundle = st.session_state.bundle
    st.subheader('Step 1: Import media')
    st.write(f"Found **{len(bundle['posts'])} posts** and **{len(bundle['media'])} media URLs** using {bundle['method']}.")
    st.download_button('Download Media XML', media_xml(bundle), 'media.xml', 'application/xml')
    if st.button('Prepare independent media backup ZIP'):
        try:
            with st.spinner('Downloading original media...'):
                z, count, warnings = backup_zip(bundle, progress)
            st.session_state.media_zip = z
            st.session_state.backup_warnings = warnings
            st.success(f'Backed up {count} of {len(bundle["media"])} files.')
        except ExportError as exc:
            st.error(str(exc))
    if 'media_zip' in st.session_state:
        st.download_button('Download media backup ZIP', st.session_state.media_zip, 'media-backup.zip', 'application/zip')
        for warning in st.session_state.get('backup_warnings', []):
            st.warning(warning)

    st.subheader('Step 2: Verify Toggle media')
    st.write('Toggle does not have to provide `/wp-json/wp/v2/media`. Automatic discovery checks only publicly exposed destination assets. The complete private Media Library may not be discoverable.')
    with st.form('verify'):
        destination = st.text_input('Toggle website URL', value=st.session_state.get('destination', ''), placeholder='https://client.brightervisionpreview.com/')
        confirmed = st.checkbox('I imported the Media XML into Toggle and enabled attachment downloads.')
        verify = st.form_submit_button('Check publicly discoverable media')
    if verify:
        try:
            if not confirmed:
                raise ExportError('Confirm the media import first.')
            destination = checked_url(destination.strip())
            with st.spinner('Inspecting publicly exposed destination media...'):
                candidates, notes = discover_public_media(destination, progress)
                mapping, missing, ambiguous = match_public_media(bundle, candidates)
            st.session_state.destination = destination
            st.session_state.mapping = mapping
            st.session_state.discovery_report = {'found':len(candidates),'missing':missing,'ambiguous':ambiguous,'notes':notes}
            st.session_state.verified = False
            st.session_state.verification_mode = 'discovery'
            st.success(f'Found {len(candidates)} public asset URLs; confidently matched {len(mapping)} of {len(bundle["media"])} source media URLs.')
            if missing or ambiguous:
                st.warning('Unmatched files are not necessarily failed imports. Toggle may keep Media Library files private until used on a public page.')
        except (ExportError, ValueError) as exc:
            st.error(str(exc))

    report = st.session_state.get('discovery_report')
    if report:
        st.write(f"**Public discovery:** {len(st.session_state.get('mapping', {}))} matched; {len(report['missing'])} not discoverable; {len(report['ambiguous'])} ambiguous.")
        for note in report['notes'][:5]:
            st.caption(note)

    st.markdown('#### Manual verification override')
    st.caption('This allows progress when Toggle does not expose its Media Library publicly. It does not claim that unmatched files were successfully imported.')
    with st.form('manual_override'):
        manual_url = st.text_input('Toggle destination URL', value=st.session_state.get('destination', destination if verify else ''), placeholder='https://client.brightervisionpreview.com/')
        manual_confirm = st.checkbox('I reviewed the imported files and failures in Toggle Media Library.')
        manual_risk = st.checkbox('I understand that unverified media URLs will remain unchanged and featured images may need review in Toggle.')
        override = st.form_submit_button('Continue with manual verification override', type='primary')
    if override:
        try:
            target = checked_url(manual_url.strip())
            if not (manual_confirm and manual_risk):
                raise ExportError('Both confirmations are required.')
            if st.session_state.get('destination') != target:
                st.session_state.mapping = {}
                st.session_state.pop('discovery_report', None)
            st.session_state.destination = target
            st.session_state.verified = True
            st.session_state.verification_mode = 'manual'
            st.success('Manual override enabled. No individual image mapping is required.')
        except (ExportError, ValueError) as exc:
            st.error(str(exc))

    st.subheader('Step 3: Export posts')
    if st.session_state.get('verified'):
        mapping = st.session_state.get('mapping', {})
        missing = [u for u in bundle['media'] if u not in mapping]
        st.write(f'**{len(mapping)} automatically matched**; **{len(missing)} not automatically verified**.')
        if missing:
            st.warning('Unverified references retain their source URLs. Do not retire the original website until destination-hosted media and featured images are confirmed.')
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(['source_url','status'])
            writer.writerows((url,'not automatically verified') for url in missing)
            st.download_button('Download media exception report', buf.getvalue().encode('utf-8-sig'), 'media-exceptions.csv', 'text/csv')
        st.info('No Toggle attachment IDs are guessed. Check featured images after importing posts.')
        st.download_button('Download Posts XML', posts_xml(bundle, mapping, st.session_state.destination), 'posts.xml', 'application/xml', type='primary')
    else:
        st.caption('Use manual verification override to continue, even if public discovery cannot find the imported media.')

st.divider()
st.caption('Public matching is best-effort, not a full Toggle Media Library audit. No Toggle credentials are collected.')
