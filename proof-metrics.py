import re
import argparse
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import matplotlib.dates as mdates
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading
import os
import io
import base64
from urllib.parse import parse_qs, urlparse
import json
import urllib.parse
import numpy as np

class LogGraphHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, log_file_path=None, **kwargs):
        self.log_file_path = log_file_path
        self.block_boundaries_cache = None
        self.block_metadata_cache = None
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        
        if parsed_url.path == '/' or parsed_url.path == '/index.html':
            query_params = parse_qs(parsed_url.query)
            
            # Get parameters for block filtering
            block_id = query_params.get('block', [None])[0]
            timestamp_str = query_params.get('timestamp', [None])[0]
            
            timestamp = None
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', ''))
                except ValueError:
                    pass
            
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            # Generate plot with the specified filters
            img_data, block_boundaries, total_range, current_block_id, block_stats = self.generate_plot(block_id, timestamp)
            
            # Create HTML with the embedded image and controls
            html = self.generate_html(img_data, block_boundaries, total_range, current_block_id, block_stats)
            
            self.wfile.write(html.encode())
        
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/find_block':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            
            # Print debugging info
            print(f"Received POST data: {post_data}")
            
            # Try to handle the data, regardless of format
            log_line = None
            
            # Try to parse as JSON
            try:
                json_data = json.loads(post_data)
                log_line = json_data.get('log_line', '')
                print(f"Parsed as JSON: {log_line}")
            except json.JSONDecodeError:
                # If JSON parsing fails, try to extract the log_line directly
                print("JSON parsing failed, trying form data...")
                if 'log_line=' in post_data:
                    log_line = post_data.split('log_line=')[1]
                    # Handle URL encoding if present
                    log_line = urllib.parse.unquote_plus(log_line)
                    print(f"Extracted from form data: {log_line}")
            
            if not log_line:
                print("Could not extract log_line from the request")
                log_line = post_data
                print(f"Using raw post data as log line: {log_line}")
            
            # Extract timestamp
            timestamp = extract_timestamp_from_log(log_line)
            print(f"Extracted timestamp: {timestamp}")
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            if timestamp:
                block_id = self.find_block_for_timestamp(timestamp)
                print(f"Found block ID: {block_id}")
                response_data = {'success': True, 'timestamp': timestamp.isoformat(), 'block_id': block_id}
            else:
                print("No timestamp found")
                response_data = {'success': False, 'error': 'No timestamp found in log line'}
                
            self.wfile.write(json.dumps(response_data).encode())
        else:
            self.send_error(404)

    def find_block_for_timestamp(self, timestamp):
        # Use cached block boundaries if available, otherwise calculate them
        if self.block_boundaries_cache is None:
            self.block_boundaries_cache, self.block_metadata_cache = self.calculate_block_boundaries()
        
        for i, (_, start, end) in enumerate(self.block_boundaries_cache):
            start_dt = datetime.fromisoformat(start.replace('Z', ''))
            end_dt = datetime.fromisoformat(end.replace('Z', ''))
            
            if start_dt <= timestamp <= end_dt:
                return str(i)
        
        return None

    def calculate_block_boundaries(self):
        # Parse log file to get timestamps and metrics
        timestamps, proofs_processed, _, _ = parse_log(self.log_file_path)
        
        if not timestamps:
            return [], {}
        
        # Extract block metadata from log file
        block_metadata = extract_block_metadata(self.log_file_path)
        
        # Identify block boundaries by looking for resets in proofs_processed
        block_boundaries = []
        current_block_start = timestamps[0]
        
        for i in range(1, len(timestamps)):
            # If proofs_processed drops significantly or goes to zero, it's likely a new block
            if proofs_processed[i] < proofs_processed[i-1] * 0.5 or proofs_processed[i] == 0:
                block_boundaries.append((
                    len(block_boundaries),
                    current_block_start.isoformat(),
                    timestamps[i-1].isoformat()
                ))
                current_block_start = timestamps[i]
        
        # Add the last block
        if current_block_start != timestamps[-1]:
            block_boundaries.append((
                len(block_boundaries),
                current_block_start.isoformat(),
                timestamps[-1].isoformat()
            ))
            
        return block_boundaries, block_metadata

    def generate_html(self, img_data, block_boundaries, total_range, current_block_id, block_stats):
        # Convert block boundaries to JavaScript format
        block_options = ""
        for i, (block, start, end) in enumerate(block_boundaries):
            block_options += f'<option value="{i}" {("selected" if str(i) == current_block_id else "")}>{start} - {end} (Block {i})</option>'
        
        # Add block statistics if available
        stats_html = ""
        if block_stats:
            duration_ms = block_stats.get('duration_ms', 0)
            avg_proofs = block_stats.get('avg_proofs_processed', 0)
            
            # Include block number and additional timing info if available
            block_number = block_stats.get('block_number', 'Unknown')
            block_hash = block_stats.get('block_hash', 'Unknown')
            elapsed_time = block_stats.get('elapsed_time', 'N/A')
            root_elapsed = block_stats.get('root_elapsed', 'N/A')
            total_time = block_stats.get('total_time', 'N/A')
            
            # If we have a total_time from "All proofs processed" log, use that instead
            processing_duration = total_time if total_time != 'N/A' else f"{duration_ms:.2f} ms"
            
            stats_html = f'''
            <div class="block-stats">
                <h3>Block Statistics</h3>
                <table>
                    <tr>
                        <td><strong>Block Number:</strong></td>
                        <td>{block_number}</td>
                    </tr>
                    <tr>
                        <td><strong>Block Hash:</strong></td>
                        <td>{block_hash}</td>
                    </tr>
                    <tr>
                        <td><strong>Processing Duration:</strong></td>
                        <td>{processing_duration}</td>
                    </tr>
                    <tr>
                        <td><strong>Block Elapsed Time:</strong></td>
                        <td>{elapsed_time}</td>
                    </tr>
                </table>
            </div>
            '''
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Proof Processing Metrics</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1 {{ color: #333; }}
                h3 {{ margin-top: 0; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                .graph {{ width: 100%; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
                .controls {{ margin: 20px 0; padding: 15px; background-color: #f8f9fa; border-radius: 5px; }}
                .info {{ margin-top: 20px; padding: 10px; background-color: #f8f9fa; border-radius: 5px; }}
                label {{ display: block; margin-bottom: 5px; font-weight: bold; }}
                select, button {{ padding: 8px; margin-right: 15px; }}
                textarea {{ width: 100%; height: 80px; margin-bottom: 10px; padding: 8px; font-family: monospace; }}
                .button-group {{ margin-top: 10px; }}
                button {{ cursor: pointer; padding: 8px 15px; background-color: #4CAF50; color: white; border: none; border-radius: 4px; }}
                button:hover {{ background-color: #45a049; }}
                .description {{ margin-bottom: 15px; }}
                .block-nav {{ display: flex; align-items: center; margin-bottom: 15px; }}
                .block-nav button {{ margin: 0 10px; }}
                .error {{ color: #d32f2f; margin-top: 5px; display: none; }}
                .block-stats {{ margin-top: 15px; }}
                .block-stats table {{ border-collapse: collapse; width: 100%; }}
                .block-stats td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
                .block-stats td:first-child {{ width: 40%; font-weight: bold; }}
            </style>
            <script>
                function updateBlock() {{
                    const blockSelect = document.getElementById('blockSelect');
                    if (blockSelect.value !== '') {{
                        window.location.href = '/?block=' + blockSelect.value;
                    }}
                }}
                
                function previousBlock() {{
                    const blockSelect = document.getElementById('blockSelect');
                    const currentIndex = blockSelect.selectedIndex;
                    if (currentIndex > 0) {{
                        blockSelect.selectedIndex = currentIndex - 1;
                        updateBlock();
                    }}
                }}
                
                function nextBlock() {{
                    const blockSelect = document.getElementById('blockSelect');
                    const currentIndex = blockSelect.selectedIndex;
                    if (currentIndex < blockSelect.options.length - 1) {{
                        blockSelect.selectedIndex = currentIndex + 1;
                        updateBlock();
                    }}
                }}
                
                function findBlockFromLog() {{
                    const logInput = document.getElementById('logInput').value.trim();
                    const errorMsg = document.getElementById('errorMsg');
                    errorMsg.style.display = 'none';
                    
                    if (!logInput) {{
                        errorMsg.textContent = 'Please paste a log line.';
                        errorMsg.style.display = 'block';
                        return;
                    }}
                    
                    // Show that we're processing
                    errorMsg.textContent = 'Processing...';
                    errorMsg.style.display = 'block';
                    
                    // Create a URLSearchParams object for the form data
                    const params = new URLSearchParams();
                    params.append('log_line', logInput);
                    
                    fetch('/find_block', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/x-www-form-urlencoded',
                        }},
                        body: params.toString()
                    }})
                    .then(response => {{
                        if (!response.ok) {{
                            throw new Error(`HTTP error! Status: ${{response.status}}`);
                        }}
                        return response.json();
                    }})
                    .then(data => {{
                        if (data.success) {{
                            if (data.block_id !== null) {{
                                window.location.href = '/?block=' + data.block_id;
                            }} else {{
                                errorMsg.textContent = 'No block found containing this timestamp.';
                                errorMsg.style.display = 'block';
                            }}
                        }} else {{
                            errorMsg.textContent = data.error || 'Unknown error occurred';
                            errorMsg.style.display = 'block';
                        }}
                    }})
                    .catch(error => {{
                        errorMsg.textContent = 'Error: ' + error.message;
                        errorMsg.style.display = 'block';
                        console.error('Error:', error);
                    }});
                }}
            </script>
        </head>
        <body>
            <div class="container">
                <h1>Proof Processing Metrics</h1>
                
                <div class="controls">
                    <div class="description">
                        <p>This visualization shows raw proof processing metrics from your log file.</p>
                    </div>
                    
                    <div>
                        <label for="logInput">Paste a log line to find its block:</label>
                        <textarea id="logInput" placeholder="Paste a log line containing a timestamp, e.g.: 2025-03-17T23:28:59.974405Z DEBUG engine::root: Prefetching proofs..."></textarea>
                        <div id="errorMsg" class="error"></div>
                        <button onclick="findBlockFromLog()">Find Block</button>
                    </div>
                    
                    <div style="margin-top: 20px;">
                        <label for="blockSelect">Or select a block directly:</label>
                        <div class="block-nav">
                            <button onclick="previousBlock()">&lt; Previous</button>
                            <select id="blockSelect" onchange="updateBlock()" style="flex-grow: 1;">
                                <option value="">-- All Blocks --</option>
                                {block_options}
                            </select>
                            <button onclick="nextBlock()">Next &gt;</button>
                        </div>
                    </div>
                    
                    {stats_html}
                </div>
                
                <div>
                    <img src="data:image/png;base64,{img_data}" class="graph" alt="Proof Metrics Graph">
                </div>
                
                <div class="info">
                    <p>Analyzing log file: <strong>{self.log_file_path}</strong></p>
                </div>
            </div>
        </body>
        </html>
        '''

    def generate_plot(self, block_id=None, timestamp=None, generate_image=True):
        # Use cached block boundaries if available, otherwise calculate them
        if self.block_boundaries_cache is None:
            self.block_boundaries_cache, self.block_metadata_cache = self.calculate_block_boundaries()
            
        block_boundaries = self.block_boundaries_cache
        block_metadata = self.block_metadata_cache
        
        # Parse log file to get all data
        timestamps, proofs_processed, state_update_proofs_requested, prefetch_proofs_requested = parse_log(self.log_file_path)
        
        if not timestamps:
            return "No data found", [], [datetime.now(), datetime.now()], None, None
        
        # If timestamp is provided, find the block that contains it
        current_block_id = None
        if timestamp and not block_id:
            for i, (block, start, end) in enumerate(block_boundaries):
                start_dt = datetime.fromisoformat(start.replace('Z', ''))
                end_dt = datetime.fromisoformat(end.replace('Z', ''))
                
                if start_dt <= timestamp <= end_dt:
                    block_id = str(i)
                    current_block_id = block_id
                    break
        
        # Filter data based on parameters
        filtered_indices = list(range(len(timestamps)))
        
        # Apply block filter if specified
        block_stats = None
        if block_id is not None and block_id.isdigit():
            block_idx = int(block_id)
            current_block_id = block_id
            if 0 <= block_idx < len(block_boundaries):
                block_start = datetime.fromisoformat(block_boundaries[block_idx][1].replace('Z', ''))
                block_end = datetime.fromisoformat(block_boundaries[block_idx][2].replace('Z', ''))
                
                filtered_indices = [i for i in filtered_indices 
                                     if block_start <= timestamps[i] <= block_end]
                
                # Calculate block statistics
                block_stats = calculate_block_statistics(
                    [timestamps[i] for i in filtered_indices],
                    [proofs_processed[i] for i in filtered_indices]
                )
                
                # Add metadata if available
                if block_metadata:
                    # Using improved metadata lookup function
                    block_info = find_block_metadata_for_time_range(
                        block_metadata,
                        block_start,
                        block_end
                    )
                    
                    if block_info:
                        # Add metadata to stats
                        block_stats.update(block_info)
                        
                        # Debug info
                        print(f"Found metadata for block: {block_info.get('block_number', 'Unknown')}")
                        print(f"  Total time: {block_info.get('total_time', 'N/A')}")
                        print(f"  Elapsed: {block_info.get('elapsed_time', 'N/A')}")
                        print(f"  Root elapsed: {block_info.get('root_elapsed', 'N/A')}")
        
        # Stop here if we're not generating an image (just returning block data)
        if not generate_image:
            return None, block_boundaries, [timestamps[0], timestamps[-1]], current_block_id, block_stats
        
        # Filter data points
        filtered_timestamps = [timestamps[i] for i in filtered_indices]
        filtered_proofs_processed = [proofs_processed[i] for i in filtered_indices]
        filtered_state_update_proofs_requested = [state_update_proofs_requested[i] for i in filtered_indices]
        filtered_prefetch_proofs_requested = [prefetch_proofs_requested[i] for i in filtered_indices]
        
        # Check if we have data after filtering
        if not filtered_timestamps:
            # Return empty image if no data
            plt.figure(figsize=(12, 6))
            plt.text(0.5, 0.5, "No data for selected filters", horizontalalignment='center', verticalalignment='center')
            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            plt.close()
            buf.seek(0)
            img_str = base64.b64encode(buf.read()).decode('utf-8')
            return img_str, block_boundaries, [timestamps[0], timestamps[-1]], current_block_id, block_stats
        
        # Generate plot
        plt.figure(figsize=(12, 6))
        
        # Plot raw data points (with markers)
        plt.plot(filtered_timestamps, filtered_proofs_processed, 'b.-', label='proofs_processed', linewidth=1, markersize=3)
        plt.plot(filtered_timestamps, filtered_state_update_proofs_requested, 'r.-', label='state_update_proofs_requested', linewidth=1, markersize=3)
        plt.plot(filtered_timestamps, filtered_prefetch_proofs_requested, 'g.-', label='prefetch_proofs_requested', linewidth=1, markersize=3)
        
        # Format the plot
        plt.xlabel('Time')
        plt.ylabel('Count')
        
        # Set title based on whether we're viewing a specific block
        if current_block_id is not None:
            block_idx = int(current_block_id)
            block_start = block_boundaries[block_idx][1]
            block_end = block_boundaries[block_idx][2]
            
            # Add block number to title if available
            block_number = block_stats.get('block_number', '') if block_stats else ''
            block_title = f"Block {block_number} " if block_number else ""
            
            title = f'{block_title}(ID {current_block_id}): {block_start} to {block_end} ({len(filtered_timestamps)} data points)'
        else:
            title = f'All Blocks ({len(filtered_timestamps)} data points)'
        
        plt.title(title)
        plt.legend()
        
        # Use auto-formatter for dates depending on the time span
        if filtered_timestamps:
            span = filtered_timestamps[-1] - filtered_timestamps[0]
            if span < timedelta(minutes=2):
                # Fixed: Use correct way to format with milliseconds
                plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S.%f'))
            else:
                plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        
        plt.gcf().autofmt_xdate()
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.tight_layout()
        
        # Save to buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        plt.close()
        
        # Convert buffer to base64
        buf.seek(0)
        img_str = base64.b64encode(buf.read()).decode('utf-8')
        
        return img_str, block_boundaries, [timestamps[0], timestamps[-1]], current_block_id, block_stats

def find_block_metadata_for_time_range(block_metadata, start_time, end_time):
    """
    Find the block metadata that corresponds to a specified time range.
    Since block completion logs happen after the proof processing logs,
    we look for the earliest metadata entry after the end of the proof processing.
    """
    # Convert to Python datetime objects for comparison
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time.replace('Z', ''))
    if isinstance(end_time, str):
        end_time = datetime.fromisoformat(end_time.replace('Z', ''))
    
    # First, try to find "All proofs processed" log right after our processing window
    # These logs happen right after proof processing completes
    proofs_processed_entries = []
    for timestamp_str, metadata in block_metadata.items():
        if 'total_time' in metadata:
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', ''))
                # Look for entries around our time window (slightly after end)
                if end_time - timedelta(seconds=1) <= timestamp <= end_time + timedelta(seconds=5):
                    proofs_processed_entries.append((timestamp, metadata))
            except ValueError:
                continue
    
    # Sort by timestamp and get the first one after our end time
    if proofs_processed_entries:
        proofs_processed_entries.sort(key=lambda x: x[0])
        # Get the closest one
        closest_entry = min(proofs_processed_entries, key=lambda x: abs((x[0] - end_time).total_seconds()))
        
        # Now look for block added logs after this "All proofs processed" log
        block_entries = []
        for timestamp_str, metadata in block_metadata.items():
            if 'block_number' in metadata and 'elapsed_time' in metadata:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', ''))
                    # Look right after the "All proofs processed" log
                    if closest_entry[0] <= timestamp <= closest_entry[0] + timedelta(seconds=5):
                        block_entries.append((timestamp, metadata))
                except ValueError:
                    continue
        
        if block_entries:
            block_entries.sort(key=lambda x: x[0])
            return block_entries[0][1]
        
        # If we found a proofs processed entry but no block, return that
        if 'block_number' in closest_entry[1]:
            return closest_entry[1]
        
        # Otherwise, look for any block entry close to our window
        for timestamp_str, metadata in block_metadata.items():
            if 'block_number' in metadata and 'elapsed_time' in metadata:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', ''))
                    if end_time <= timestamp <= end_time + timedelta(seconds=10):
                        return metadata
                except ValueError:
                    continue
    
    # If we still haven't found anything, look for any metadata close to our time range
    all_entries = []
    for timestamp_str, metadata in block_metadata.items():
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', ''))
            # Give a wider window to find anything relevant
            if start_time - timedelta(seconds=5) <= timestamp <= end_time + timedelta(seconds=10):
                all_entries.append((timestamp, metadata))
        except ValueError:
            continue
    
    if all_entries:
        all_entries.sort(key=lambda x: x[0])
        # Try to find an entry with block info
        for timestamp, metadata in all_entries:
            if 'block_number' in metadata:
                return metadata
        
        # If no entry with block info, just return the closest one
        closest_entry = min(all_entries, key=lambda x: abs((x[0] - end_time).total_seconds()))
        return closest_entry[1]
    
    return None

def extract_block_metadata(log_file_path):
    """
    Extract block metadata from the log file, including block numbers, hashes,
    elapsed times, and root calculation times.
    """
    block_metadata = {}
    block_timestamps = {}  # Store timestamps for each block number
    
    # Regular expressions for important log entries
    block_added_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+INFO reth_node_events::node: Block added to canonical chain number=(\d+) hash=(0x[a-f0-9]+).+?elapsed=(\S+)'
    root_elapsed_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+DEBUG engine::tree: Calculated state root root_elapsed=(\S+) block=NumHash { number: (\d+), hash: (0x[a-f0-9]+)'
    proofs_processed_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+DEBUG engine::root: All proofs processed, ending calculation.+?total_time=Some\((\S+)\)'
    
    # Extract data from log file
    with open(log_file_path, 'r') as file:
        file_content = file.read()
        
        # First collect all "All proofs processed" entries
        for match in re.finditer(proofs_processed_pattern, file_content):
            timestamp = match.group(1)
            total_time = match.group(2)
            
            # Store timestamp and total_time
            metadata = {'total_time': total_time, 'timestamp': timestamp}
            block_metadata[timestamp] = metadata
        
        # Then collect all block info entries
        for match in re.finditer(block_added_pattern, file_content):
            timestamp = match.group(1)
            block_number = match.group(2)
            block_hash = match.group(3)
            elapsed_time = match.group(4)
            
            # Check if we already have metadata for this timestamp
            if timestamp in block_metadata:
                block_metadata[timestamp].update({
                    'block_number': block_number,
                    'block_hash': block_hash,
                    'elapsed_time': elapsed_time
                })
            else:
                block_metadata[timestamp] = {
                    'block_number': block_number,
                    'block_hash': block_hash,
                    'elapsed_time': elapsed_time,
                    'timestamp': timestamp
                }
            
            # Store timestamp for this block number
            block_timestamps[block_number] = timestamp
        
        # Finally add root elapsed times
        for match in re.finditer(root_elapsed_pattern, file_content):
            timestamp = match.group(1)
            root_elapsed = match.group(2)
            block_number = match.group(3)
            block_hash = match.group(4)
            
            # If we have this timestamp already
            if timestamp in block_metadata:
                block_metadata[timestamp].update({
                    'root_elapsed': root_elapsed,
                    'block_number': block_number,
                    'block_hash': block_hash
                })
            else:
                block_metadata[timestamp] = {
                    'root_elapsed': root_elapsed,
                    'block_number': block_number,
                    'block_hash': block_hash,
                    'timestamp': timestamp
                }
            
            # Also update block number's metadata if we have it
            block_timestamp = block_timestamps.get(block_number)
            if block_timestamp and block_timestamp in block_metadata:
                block_metadata[block_timestamp]['root_elapsed'] = root_elapsed
    
    print(f"Extracted metadata for {len(block_metadata)} block-related log entries")
    return block_metadata

def calculate_block_statistics(timestamps, proofs_processed):
    """Calculate statistics for a block of data."""
    if not timestamps or len(timestamps) < 2:
        return {
            'duration_ms': 0,
            'avg_proofs_processed': 0
        }
    
    # Calculate block duration in milliseconds
    duration_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
    duration_ms = duration_seconds * 1000
    
    # Calculate the average rate of proofs processed per second
    # Use the difference between last and first values divided by time
    if duration_seconds > 0:
        proofs_rate = (proofs_processed[-1] - proofs_processed[0]) / duration_seconds
    else:
        proofs_rate = 0
    
    return {
        'duration_ms': duration_ms,
        'avg_proofs_processed': proofs_rate
    }

def extract_timestamp_from_log(log_line):
    # Extract timestamp from a log line
    timestamp_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)'
    match = re.search(timestamp_pattern, log_line)
    
    if match:
        timestamp_str = match.group(1)
        try:
            # Print for debugging
            print(f"Found timestamp: {timestamp_str}")
            return datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S.%fZ')
        except ValueError as e:
            # Print the error for debugging
            print(f"Error parsing timestamp {timestamp_str}: {str(e)}")
            
            # Try an alternative parsing approach if the standard one fails
            try:
                # Sometimes the microsecond format can cause issues
                timestamp_without_z = timestamp_str.replace('Z', '')
                dt = datetime.fromisoformat(timestamp_without_z)
                return dt
            except ValueError:
                print(f"Alternative parsing also failed for {timestamp_str}")
                return None
    
    print(f"No timestamp pattern match in: {log_line}")
    return None

def parse_log(log_file_path):
    # Initialize data structures
    timestamps = []
    proofs_processed = []
    state_update_proofs_requested = []
    prefetch_proofs_requested = []
    
    # Regular expression to extract data from "Checking end condition" log lines
    pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z) DEBUG engine::root: Checking end condition proofs_processed=(\d+) state_update_proofs_requested=(\d+) prefetch_proofs_requested=(\d+)'
    
    with open(log_file_path, 'r') as file:
        for line in file:
            match = re.search(pattern, line)
            if match:
                # Extract timestamp and convert to datetime
                timestamp_str = match.group(1)
                try:
                    timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S.%fZ')
                    
                    # Extract values
                    pp = int(match.group(2))
                    supr = int(match.group(3))
                    ppr = int(match.group(4))
                    
                    # Append to data lists
                    timestamps.append(timestamp)
                    proofs_processed.append(pp)
                    state_update_proofs_requested.append(supr)
                    prefetch_proofs_requested.append(ppr)
                except ValueError as e:
                    print(f"Error parsing log line: {e}")
                    print(f"Problematic timestamp: {timestamp_str}")
    
    print(f"Parsed {len(timestamps)} log entries from file")
    return timestamps, proofs_processed, state_update_proofs_requested, prefetch_proofs_requested

def start_server(log_file_path, port):
    # Create a custom handler with access to the log file path
    handler = lambda *args, **kwargs: LogGraphHandler(*args, log_file_path=log_file_path, **kwargs)
    
    server = HTTPServer(('localhost', port), handler)
    print(f"Server started at http://localhost:{port}")
    print(f"Analyzing log file: {log_file_path}")
    print("Press Ctrl+C to stop the server")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped.")
        server.server_close()

def main():
    parser = argparse.ArgumentParser(description='Analyze log files and display metrics on a web server')
    parser.add_argument('log_file', help='Path to the log file to analyze')
    parser.add_argument('-p', '--port', type=int, default=8000, help='Port to run the HTTP server on (default: 8000)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.log_file):
        print(f"Error: Log file '{args.log_file}' not found.")
        return
    
    # Start the HTTP server
    start_server(args.log_file, args.port)

if __name__ == "__main__":
    main()
