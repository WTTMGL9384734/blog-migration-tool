import streamlit as st
from exporter import ExportError
from combined import collect, create_xml

st.set_page_config(page_title='Blog Migration Tool',page_icon='📦')
st.title('Blog Migration Tool')
st.caption('One complete Toggle-compatible WXR XML containing blog posts and media attachments.')
st.write('Enter the source blog URL, scan its posts, and download one XML file for import into Toggle. No separate media import, verification, or mapping steps.')
with st.form('scan'):
    source=st.text_input('Source blog URL',placeholder='https://example.com/blog/')
    maximum=st.number_input('Maximum posts',min_value=1,max_value=300,value=100)
    submit=st.form_submit_button('Generate Complete Blog XML',type='primary')
if submit:
    st.session_state.pop('export',None)
    try:
        with st.spinner('Collecting full posts and media references...'):
            bundle=collect(source,int(maximum))
            output=create_xml(bundle)
            st.session_state.export=(bundle,output)
    except (ExportError,ValueError) as exc:
        st.error(str(exc))
if 'export' in st.session_state:
    bundle,output=st.session_state.export
    st.success(f"Ready: {len(bundle['posts'])} posts and {len(bundle['media'])} media attachments ({bundle['method']}).")
    st.download_button('Download Complete Blog XML',output,'complete-blog-import.xml','application/xml',type='primary')
    st.info('Import this single XML into Toggle while the source site is still live. The attachment records contain source URLs for Toggle to fetch; the XML does not contain image binaries. Verify images and featured images on imported posts before taking the source site offline.')
