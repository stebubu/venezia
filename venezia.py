import streamlit as st
import rasterio
import folium
from folium.plugins import TimestampedGeoJson
from streamlit_folium import folium_static
import numpy as np
import glob
import os
from datetime import datetime
from branca.colormap import LinearColormap
import pandas as pd
import boto3
from botocore.exceptions import NoCredentialsError
import tempfile
import rasterio
from rasterio.session import AWSSession
from urllib.parse import urlparse

def parse_s3_url(s3_url):
    """Parse S3 URL into bucket name and prefix"""
    # Remove 's3://' if present
    if s3_url.startswith('s3://'):
        s3_url = s3_url[5:]
    
    # Split into bucket and prefix
    parts = s3_url.split('/', 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ''
    
    return bucket, prefix

def get_aws_credentials():
    """
    Get AWS credentials from multiple sources in order of preference:
    1. Streamlit secrets
    2. Environment variables
    3. User input in the sidebar
    """
    aws_credentials = {
        'access_key_id': None,
        'secret_access_key': None,
        'region': None
    }
    
    # Try to get from environment variables first
    aws_credentials['access_key_id'] = os.getenv('AWS_ACCESS_KEY_ID')
    aws_credentials['secret_access_key'] = os.getenv('AWS_SECRET_ACCESS_KEY')
    aws_credentials['region'] = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
    
    # Try to get from streamlit secrets if not in env vars
    if not all([aws_credentials['access_key_id'], aws_credentials['secret_access_key']]):
        try:
            aws_credentials['access_key_id'] = st.secrets["aws"]["access_key_id"]
            aws_credentials['secret_access_key'] = st.secrets["aws"]["secret_access_key"]
            aws_credentials['region'] = st.secrets["aws"]["region"]
        except KeyError:
            pass
    
    # If still not found, ask user in sidebar
    if not all([aws_credentials['access_key_id'], aws_credentials['secret_access_key']]):
        st.sidebar.header("AWS Credentials")
        aws_credentials['access_key_id'] = st.sidebar.text_input("AWS Access Key ID")
        aws_credentials['secret_access_key'] = st.sidebar.text_input("AWS Secret Access Key", type="password")
        aws_credentials['region'] = st.sidebar.text_input("AWS Region", value="us-east-1")
    
    return aws_credentials

def list_s3_files(bucket, prefix='', credentials=None):
    """List all GeoTIFF files in the specified S3 bucket and prefix"""
    try:
        s3_client = boto3.client('s3',
            aws_access_key_id=credentials['access_key_id'],
            aws_secret_access_key=credentials['secret_access_key'],
            region_name=credentials['region']
        )
        
        # Handle empty prefix
        if prefix and not prefix.endswith('/'):
            prefix = prefix + '/'
            
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        
        files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                if obj['Key'].lower().endswith(('.tif', '.tiff')):
                    files.append(f"s3://{bucket}/{obj['Key']}")
        return files
    except NoCredentialsError:
        st.error("AWS credentials not found or invalid")
        return []
    except Exception as e:
        st.error(f"Error accessing S3: {str(e)}")
        return []

def main():
    st.title("Temporal Rainfall and Flood Map Viewer")
    
    # Get AWS credentials
    aws_credentials = get_aws_credentials()
    
    # Only proceed if we have credentials
    if all([aws_credentials['access_key_id'], aws_credentials['secret_access_key']]):
        # Initialize AWS session
        aws_session = AWSSession(
            aws_access_key_id=aws_credentials['access_key_id'],
            aws_secret_access_key=aws_credentials['secret_access_key'],
            region_name=aws_credentials['region']
        )
        
        # Sidebar controls
        st.sidebar.header("Data Source")
        
        # S3 path input
        s3_path = st.sidebar.text_input("S3 Path (e.g., s3://bucket-name/prefix/path)")
        
        if s3_path:
            try:
                # Parse S3 path
                bucket_name, prefix = parse_s3_url(s3_path)
                
                # List available files in S3
                s3_files = list_s3_files(bucket_name, prefix, aws_credentials)
                
                
                if s3_files:
                    st.sidebar.success(f"Found {len(s3_files)} GeoTIFF files")
                    
                    # Create time slider based on available files
                    selected_time_index = st.sidebar.slider(
                        "Select Time Step",
                        0,
                        len(s3_files) - 1,
                        0
                    )
                    
                    # Load the selected raster from S3
                    current_file = s3_files[selected_time_index]
                    
                    # Display current file path
                    st.sidebar.text(f"Current file: {current_file}")
                    
                    with rasterio.Env(aws_session):
                        with rasterio.open(current_file) as src:
                            data = src.read(1)
                            bounds = src.bounds
                            transform = src.transform
                            crs = src.crs
                    
                    # Create the map
                    m = folium.Map(
                        location=[(bounds.bottom + bounds.top)/2, 
                                 (bounds.left + bounds.right)/2],
                        zoom_start=10
                    )
                    
                    # Create colormap
                    colormap = create_colormap(data)
                    
                    # Add the raster layer
                    img = folium.raster_layers.ImageOverlay(
                        data,
                        bounds=[[bounds.bottom, bounds.left], 
                               [bounds.top, bounds.right]],
                        colormap=lambda x: colormap(x),
                        opacity=0.7,
                        name=f'Raster Layer {selected_time_index}'
                    )
                    img.add_to(m)
                    
                    # Add layer control
                    folium.LayerControl().add_to(m)
                    
                    # Display the map
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        folium_static(m)
                    
                    # Add pixel value identifier
                    with col2:
                        st.subheader("Pixel Value Identifier")
                        st.write("Click on the map to identify pixel values")
                        
                        # Create an empty placeholder for pixel values
                        pixel_value_container = st.empty()
                        
                        # Add click event handler
                        if st.session_state.get('last_clicked') is not None:
                            lat, lon = st.session_state.last_clicked
                            # Convert coordinates to pixel indices
                            py, px = ~transform * (lon, lat)
                            px, py = int(px), int(py)
                            
                            if 0 <= px < data.shape[1] and 0 <= py < data.shape[0]:
                                value = data[py, px]
                                pixel_value_container.write(f"""
                                    **Coordinates:** ({lat:.6f}, {lon:.6f})  
                                    **Value:** {value:.2f}
                                """)
                    
                    # Display statistics
                    st.sidebar.subheader("Statistics")
                    st.sidebar.write(f"""
                        **File:** {os.path.basename(current_file)}  
                        **Min:** {np.nanmin(data):.2f}  
                        **Max:** {np.nanmax(data):.2f}  
                        **Mean:** {np.nanmean(data):.2f}  
                        **Std:** {np.nanstd(data):.2f}
                    """)
                else:
                    st.warning("No GeoTIFF files found in the specified S3 path")
            except Exception as e:
                st.error(f"Error processing S3 path: {str(e)}")
                st.info("Please enter a valid S3 path in the format: s3://bucket-name/prefix/path")
    else:
        st.error("AWS credentials are required. Please provide them through environment variables, secrets.toml, or in the sidebar.")

def create_colormap(data, colormap_name='YlOrRd'):
    """Create a colormap based on data range"""
    vmin = np.nanmin(data)
    vmax = np.nanmax(data)
    return LinearColormap(
        colors=['yellow', 'orange', 'red'],
        vmin=vmin,
        vmax=vmax
    )

if __name__ == "__main__":
    main()
