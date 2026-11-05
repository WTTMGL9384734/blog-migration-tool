import streamlit as st
from exporter import export_site, ExportError

st.set_page_config(page_title='Blog Migration Tool', page_icon='📦', layout='centered')
st.title('Blog Migration Tool')
st.caption('Export public WordPress blogs to WordPress WXR XML with a separate media backup.')
st.warning('Import the XML while the source site is still online. The XML references image URLs; the media ZIP is a backup, not an automatic offline importer.')
with st.form('export'):
    url = st.text_input('Source website or blog URL', placeholder='https://example.com/blog/')
    max_posts = st.number_input('Maximum posts', min_value=1, max_value=300, value=100)
    include_media = st.checkbox('Download media backup', value=True)
    submitted = st.form_submit_button('Export blog', type='primary')
if submitted:
    progress = st.progress(0, text='Starting export')
    messages = st.empty()
    def report(message, fraction=None):
        messages.info(message)
        if fraction is not None:
            progress.progress(min(1.0, max(0.0, fraction)), text=message)
    try:
        result = export_site(url, int(max_posts), include_media, report)
        progress.progress(1.0, text='Export complete')
        st.success(f"Exported {result['post_count']} posts, found {result['media_count']} media URLs, downloaded {result['downloaded_count']} media files.")
        if result['warnings']:
            with st.expander(f"Warnings ({len(result['warnings'])})", expanded=True):
                for warning in result['warnings']:
                    st.write('- ' + warning)
        st.download_button('Download XML + media ZIP', result['zip'], file_name=result['filename'], mime='application/zip', type='primary')
        st.download_button('Download XML only', result['xml'], file_name=result['xml_filename'], mime='application/xml')
    except (ExportError, ValueError) as e:
        st.error(str(e))
    except Exception as e:
        st.error(f'Export failed: {e}')
st.divider()
st.caption('Works best with publicly accessible WordPress REST API posts. HTML fallback is best-effort and may need adjustments for some themes. Only export content you are authorized to migrate.')
