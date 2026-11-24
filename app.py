import streamlit as st
from exporter import ExportError
from combined import collect, create_xml
from formatter import format_wxr

st.set_page_config(page_title='Blog Migration Tool',page_icon='📦')
st.title('Blog Migration Tool')
st.caption('Prepare a single Toggle-compatible WordPress WXR XML with posts and media attachments.')
maximum=st.number_input('Maximum posts and media files (each)',min_value=1,max_value=1500,value=100)
scan,upload=st.tabs(['Scan a website','Upload & Reformat XML'])
with scan:
    st.write('Scan the existing blog and generate one Complete Blog XML for Toggle.')
    with st.form('scan'):
        source=st.text_input('Source blog URL',placeholder='https://example.com/blog/')
        submit=st.form_submit_button('Generate Complete Blog XML',type='primary')
    if submit:
        st.session_state.pop('export',None)
        try:
            with st.spinner('Collecting full posts and media references...'):
                bundle=collect(source,int(maximum));output=create_xml(bundle)
                st.session_state.export=(bundle,output)
        except (ExportError,ValueError) as exc: st.error(str(exc))
    if 'export' in st.session_state:
        bundle,output=st.session_state.export
        st.success(f"Ready: {len(bundle['posts'])} posts and {len(bundle['media'])} media attachments ({bundle['method']}).")
        st.download_button('Download Complete Blog XML',output,'complete-blog-import.xml','application/xml',type='primary')
with upload:
    st.write('Upload an existing WordPress WXR XML. The formatter retains post metadata and attachment IDs, removes unnecessary HTML styling, normalizes supported media URLs, and merges duplicate media records.')
    file=st.file_uploader('WordPress WXR XML file',type=['xml'],key='source_xml')
    if file and st.button('Reformat XML',type='primary'):
        try:
            with st.spinner('Formatting XML...'):
                result,report=format_wxr(file.getvalue(), max_posts=int(maximum), max_media=int(maximum))
                st.session_state.formatted=(result,report,file.name)
        except ValueError as exc:
            st.session_state.pop('formatted',None);st.error(str(exc))
    if 'formatted' in st.session_state:
        result,report,name=st.session_state.formatted
        st.success(f"Ready: {report['posts']} posts, {report['attachments_after']} media attachments. {report['duplicate_media_removed']} duplicate attachment records removed.")
        if report['other_records']: st.info(f"Preserved {report['other_records']} additional XML records (such as pages or custom types).")
        if report['duplicates']:
            with st.expander('Duplicate media URLs merged'):
                for u in report['duplicates']: st.code(u)
        st.download_button('Download Reformatted Complete Blog XML',result,'reformatted-complete-blog-import.xml','application/xml',type='primary')
st.info('Keep the source website live while importing. XML attachment records contain URLs, not embedded media binaries. Verify imported posts, inline images, and featured images in Toggle before retiring the original site.')
