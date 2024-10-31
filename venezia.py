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
        'region': 'us-east-1'
    }
    
    # Try to get from streamlit secrets
    try:
        aws_credentials['access_key_id'] = st.secrets["aws"]["access_key_id"]
        aws_credentials['secret_access_key'] = st.secrets["aws"]["secret_access_key"]
        aws_credentials['region'] = st.secrets["aws"]["region"]
        return aws_credentials
    except KeyError:
        pass
    
    # Try to get from environment variables
    aws_credentials['access_key_id'] = os.getenv('AWS_ACCESS_KEY_ID')
    aws_credentials['secret_access_key'] = os.getenv('AWS_SECRET_ACCESS_KEY')
    aws_credentials['region'] = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
    
    # If still not found, ask user in sidebar
    if not all([aws_credentials['access_key_id'], aws_credentials['secret_access_key']]):
        st.sidebar.header("AWS Credentials")
        aws_credentials['access_key_id'] = st.sidebar.text_input("AWS Access Key ID")
        aws_credentials['secret_access_key'] = st.sidebar.text_input("AWS Secret Access Key", type="password")
        aws_credentials['region'] = st.sidebar.text_input("AWS Region", value="us-east-1")
    
    return aws_credentials

def configure_aws(credentials):
    """Configure AWS credentials"""
    # Configure boto3 client
    s3_client = boto3.client(
        's3',
        aws_access_key_id=credentials['access_key_id'],
        aws_secret_access_key=credentials['secret_access_key'],
        region_name=credentials['region']
    )
    
    # Configure rasterio AWS session
    aws_session = AWSSession(
        aws_access_key_id=credentials['access_key_id'],
        aws_secret_access_key=credentials['secret_access_key'],
        region_name=credentials['region']
    )
    
    return s3_client, aws_session

def list_s3_files(bucket, prefix='', credentials=None):
    """List all GeoTIFF files in the specified S3 bucket and prefix"""
    try:
        s3_client = boto3.client('s3',
            aws_access_key_id=credentials['access_key_id'],
            aws_secret_access_key=credentials['secret_access_key'],
            region_name=credentials['region']
        )
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

def load_raster_from_s3(s3_path, aws_session):
    """Load raster data from S3 using rasterio with AWS session"""
    try:
        with rasterio.Env(aws_session):
            with rasterio.open(s3_path) as src:
                data = src.read(1)  # Read the first band
                bounds = src.bounds
                transform = src.transform
                crs = src.crs
        return data, bounds, transform, crs
    except Exception as e:
        st.error(f"Error reading raster from S3: {str(e)}")
        return None, None, None, None

def create_colormap(data, colormap_name='YlOrRd'):
    """Create a colormap based on data range"""
    vmin = np.nanmin(data)
    vmax = np.nanmax(data)
    return LinearColormap(
        colors=['yellow', 'orange', 'red'],
        vmin=vmin,
        vmax=vmax
    )

def main():
    st.title("Temporal Rainfall and Flood Map Viewer")
    
    # Get AWS credentials
    aws_credentials = get_aws_credentials()
    
    # Only proceed if we have credentials
    if all([aws_credentials['access_key_id'], aws_credentials['secret_access_key']]):
        # Initialize AWS configuration
        s3_client, aws_session = configure_aws(aws_credentials)
        
        # Sidebar controls
        st.sidebar.header("Data Source")
        
        # S3 bucket and prefix input
        bucket_name = st.sidebar.text_input("S3 Bucket Name")
        prefix = st.sidebar.text_input("S3 Prefix (optional)")
        
        # Map background selection
        background_options = {
            "OpenStreetMap": "OpenStreetMap",
            "Google Maps": "Google Maps",
            "Mapbox": "Mapbox"
        }
        selected_background = st.sidebar.selectbox(
            "Select Map Background",
            list(background_options.keys())
        )
        
        if bucket_name:
            # List available files in S3
            s3_files = list_s3_files(bucket_name, prefix, aws_credentials)
            
            if s3_files:
                # Rest of the code remains the same...
                # [Previous map rendering and visualization code]
                # Create time slider based on available files
                selected_time_index = st.sidebar.slider(
                    "Select Time Step",
                    0,
                    len(s3_files) - 1,
                    0
                )
                
                # Load the selected raster from S3
                current_file = s3_files[selected_time_index]
                data, bounds, transform, crs = load_raster_from_s3(current_file, aws_session)
                
                if data is not None:
                    # Create the map
                    m = folium.Map(
                        location=[(bounds.bottom + bounds.top)/2, 
                                 (bounds.left + bounds.right)/2],
                        zoom_start=10
                    )
                    
                    # Add the selected background layer
                    if selected_background == "Google Maps":
                        folium.TileLayer(
                            tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
                            attr='Google Maps',
                            name='Google Maps'
                        ).add_to(m)
                    elif selected_background == "Mapbox":
                        if "mapbox" in st.secrets and "token" in st.secrets["mapbox"]:
                            mapbox_token = st.secrets["mapbox"]["token"]
                            folium.TileLayer(
                                tiles=f'https://api.mapbox.com/styles/v1/mapbox/streets-v11/tiles/{{z}}/{{x}}/{{y}}?access_token={mapbox_token}',
                                attr='Mapbox',
                                name='Mapbox'
                            ).add_to(m)
                        else:
                            st.warning("Mapbox token not configured. Defaulting to OpenStreetMap.")
                    
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
                    
                    # Display statistics and metadata
                    st.sidebar.subheader("Statistics")
                    st.sidebar.write(f"""
                        **File:** {os.path.basename(current_file)}  
                        **Min:** {np.nanmin(data):.2f}  
                        **Max:** {np.nanmax(data):.2f}  
                        **Mean:** {np.nanmean(data):.2f}  
                        **Std:** {np.nanstd(data):.2f}
                    """)
            else:
                st.warning("No GeoTIFF files found in the specified S3 bucket/prefix")
    else:
        st.error("AWS credentials are required. Please provide them in the sidebar or configure them through environment variables or secrets.toml")

if __name__ == "__main__":
    main()
