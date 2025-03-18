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
import matplotlib.ticker as mticker

class LogComparisonHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, log_file_1=None, log_file_2=None, single_file=False, **kwargs):
        self.log_file_1 = log_file_1
        self.log_file_2 = log_file_2
        self.single_file = single_file
        self.block_metadata_cache_1 = None
        self.block_metadata_cache_2 = None
        self.block_numbers_cache = None
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed_url = urlparse(self.path)

        if parsed_url.path == '/' or parsed_url.path == '/index.html':
            query_params = parse_qs(parsed_url.query)

            # Get parameters for block filtering
            block_number = query_params.get('block', [None])[0]

            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            # If no block metadata cache, build it
            if self.block_metadata_cache_1 is None:
                if self.single_file:
                    # For single file mode, extract both runs from one file
                    self.block_metadata_cache_1, self.block_metadata_cache_2 = extract_block_metadata_from_single_file(self.log_file_1)
                else:
                    # For two-file mode, extract separately
                    self.block_metadata_cache_1 = extract_block_metadata(self.log_file_1)
                    self.block_metadata_cache_2 = extract_block_metadata(self.log_file_2)

                self.block_numbers_cache = self.get_common_block_numbers()

            # Generate comparison data and plots
            if block_number:
                # Generate individual block comparison
                block_data, comparison_img = self.generate_block_comparison(block_number)
                html = self.generate_block_comparison_html(block_data, comparison_img)
            else:
                # Generate overview comparison
                overview_img = self.generate_overview_comparison()
                html = self.generate_overview_html(overview_img)

            self.wfile.write(html.encode())

        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/find_block':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')

            # Try to handle the data, regardless of format
            log_line = None

            # Try to parse as JSON
            try:
                json_data = json.loads(post_data)
                log_line = json_data.get('log_line', '')
            except json.JSONDecodeError:
                # If JSON parsing fails, try to extract the log_line directly
                if 'log_line=' in post_data:
                    log_line = post_data.split('log_line=')[1]
                    # Handle URL encoding if present
                    log_line = urllib.parse.unquote_plus(log_line)

            if not log_line:
                log_line = post_data

            # Extract block number from log line
            block_number = extract_block_number_from_log(log_line)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            if block_number:
                response_data = {'success': True, 'block_number': block_number}
            else:
                response_data = {'success': False, 'error': 'No block number found in log line'}

            self.wfile.write(json.dumps(response_data).encode())
        else:
            self.send_error(404)

    def get_common_block_numbers(self):
        """Get block numbers that appear in both runs."""
        if self.block_metadata_cache_1 is None or self.block_metadata_cache_2 is None:
            return []

        # Get block numbers from each run
        blocks_1 = set()
        blocks_2 = set()

        for _, metadata in self.block_metadata_cache_1.items():
            if 'block_number' in metadata:
                blocks_1.add(metadata['block_number'])

        for _, metadata in self.block_metadata_cache_2.items():
            if 'block_number' in metadata:
                blocks_2.add(metadata['block_number'])

        # Find common blocks
        common_blocks = blocks_1.intersection(blocks_2)

        # Sort numerically
        sorted_blocks = sorted(common_blocks, key=lambda x: int(x))

        return sorted_blocks

    def generate_overview_comparison(self):
        """Generate a comparison chart of all common blocks using cached metadata."""
        if not self.block_numbers_cache:
            print("No common blocks found between the two runs")
            return self.generate_empty_chart("No common blocks found between the two runs")

        print(f"Generating overview comparison for {len(self.block_numbers_cache)} blocks")
        
        # Limit the number of blocks to process to avoid performance issues
        MAX_BLOCKS = 100
        if len(self.block_numbers_cache) > MAX_BLOCKS:
            print(f"Too many blocks ({len(self.block_numbers_cache)}), limiting to {MAX_BLOCKS}")
            processed_blocks = self.block_numbers_cache[:MAX_BLOCKS]
        else:
            processed_blocks = self.block_numbers_cache

        # Collect data for each common block directly from the metadata cache
        block_numbers = []
        total_times_1 = []
        total_times_2 = []
        elapsed_times_1 = []
        elapsed_times_2 = []
        elapsed_time_diffs = []  # Store differences between runs

        # First, organize metadata by block number for easier lookup
        run1_by_block = {}
        run2_by_block = {}
        
        for timestamp, metadata in self.block_metadata_cache_1.items():
            if 'block_number' in metadata:
                block_num = metadata['block_number']
                if block_num not in run1_by_block:
                    run1_by_block[block_num] = metadata
        
        for timestamp, metadata in self.block_metadata_cache_2.items():
            if 'block_number' in metadata:
                block_num = metadata['block_number']
                if block_num not in run2_by_block:
                    run2_by_block[block_num] = metadata

        print(f"Organized metadata by block number: Run1: {len(run1_by_block)}, Run2: {len(run2_by_block)}")

        # Now process each block using the cached metadata
        for block_number in processed_blocks:
            print(f"Processing block {block_number}")
            try:
                # Look up data for each run in our organized cache
                data_1 = run1_by_block.get(block_number)
                data_2 = run2_by_block.get(block_number)

                # Only add blocks with data in both runs
                if data_1 and data_2:
                    block_numbers.append(block_number)

                    # Extract millisecond values with safe defaults
                    # For total time, try both 'total_time' (old format) and 'processing_time' (new format)
                    total_1 = self.extract_ms_value(data_1.get('total_time', data_1.get('processing_time', 'N/A'))) or 0
                    total_2 = self.extract_ms_value(data_2.get('total_time', data_2.get('processing_time', 'N/A'))) or 0
                    elapsed_1 = self.extract_ms_value(data_1.get('elapsed_time', 'N/A')) or 0
                    elapsed_2 = self.extract_ms_value(data_2.get('elapsed_time', 'N/A')) or 0

                    # Calculate the difference (run1 - run2)
                    # A positive value means run2 was faster, negative means run1 was faster
                    elapsed_diff = elapsed_1 - elapsed_2

                    total_times_1.append(total_1)
                    total_times_2.append(total_2)
                    elapsed_times_1.append(elapsed_1)
                    elapsed_times_2.append(elapsed_2)
                    elapsed_time_diffs.append(elapsed_diff)
                    
                    print(f"Block {block_number} - Run1: {total_1}ms, {elapsed_1}ms | Run2: {total_2}ms, {elapsed_2}ms | Diff: {elapsed_diff}ms")
                else:
                    print(f"Block {block_number} missing data in one or both runs")
            except Exception as e:
                print(f"Error processing block {block_number}: {str(e)}")

        if not block_numbers:
            print("No blocks with complete timing data found")
            return self.generate_empty_chart("No blocks with complete timing data found")

        print(f"Creating chart for {len(block_numbers)} blocks")

        # Create figure with two subplots
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

        # Processing Time Comparison (top plot) - Keep this as a comparison
        x = np.arange(len(block_numbers))
        width = 0.35

        # Bar charts for processing time comparison
        bars1 = ax1.bar(x - width/2, total_times_1, width, label='Run 1 Processing Time', color='skyblue')
        bars2 = ax1.bar(x + width/2, total_times_2, width, label='Run 2 Processing Time', color='salmon')

        # Add improvement percentage (positive = better in run 2)
        for i in range(len(block_numbers)):
            if total_times_1[i] > 0 and total_times_2[i] > 0:
                improvement = (total_times_1[i] - total_times_2[i]) / total_times_1[i] * 100
                if abs(improvement) > 5:  # Only show significant changes
                    color = 'green' if improvement > 0 else 'red'
                    ax1.annotate(f"{improvement:.1f}%",
                                xy=(i, max(total_times_1[i], total_times_2[i]) * 1.05),
                                ha='center', va='bottom', color=color, fontsize=9,
                                rotation=90 if abs(improvement) > 20 else 0)

        ax1.set_ylabel('Processing Time (ms)')
        ax1.set_title('Proof Processing Time Comparison Between Runs')
        ax1.legend()

        # Block Elapsed Time Difference (bottom plot) - Display the difference
        # Define colors based on difference value
        colors = ['green' if diff > 0 else 'red' for diff in elapsed_time_diffs]
        
        # Bar chart for differences
        bars3 = ax2.bar(x, elapsed_time_diffs, width, color=colors)

        # Add value labels
        for i, v in enumerate(elapsed_time_diffs):
            if v != 0:
                ax2.annotate(f"{v:.1f}ms",
                            xy=(i, v),
                            xytext=(0, 3 if v > 0 else -10),
                            textcoords="offset points",
                            ha='center',
                            va='bottom' if v > 0 else 'top',
                            fontsize=9)

        # Add a horizontal line at y=0
        ax2.axhline(y=0, color='black', linestyle='-', alpha=0.3)

        # Label the axes
        ax2.set_xlabel('Block Number')
        ax2.set_ylabel('Elapsed Time Difference (ms)')
        ax2.set_title('Block Elapsed Time Difference (Run 1 - Run 2)\nPositive = Run 2 is faster, Negative = Run 1 is faster')

        # X-ticks as block numbers
        plt.xticks(x, block_numbers, rotation=90)

        # Adjust layout
        plt.tight_layout()

        print("Rendering chart image")
        # Convert plot to base64 image
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        plt.close(fig)
        buf.seek(0)
        img_str = base64.b64encode(buf.read()).decode('utf-8')
        print("Chart rendering complete")

        return img_str

    def get_block_data_from_logs(self, block_number, is_first_run=True):
        """Extract block data directly from log lines for more accurate results."""
        # Determine boundary time for single file mode
        boundary_time = None
        if self.single_file:
            # Try a different approach to find a boundary time
            # Look for all occurrences of this block in the logs
            all_block_logs = []
            
            with open(self.log_file_1, 'r') as file:
                for line in file:
                    if f'Block added to canonical chain number={block_number}' in line:
                        timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)', line)
                        if timestamp_match:
                            all_block_logs.append(timestamp_match.group(1))
            
            # If we found multiple occurrences, use the midpoint as boundary
            if len(all_block_logs) >= 2:
                time1 = datetime.fromisoformat(all_block_logs[0].replace('Z', ''))
                time2 = datetime.fromisoformat(all_block_logs[1].replace('Z', ''))
                boundary_time = time1 + (time2 - time1) / 2
        
        # New, fixed line
        log_file = self.log_file_1 if self.single_file else (self.log_file_1 if is_first_run else self.log_file_2)
        
        # Extract log lines for this block
        logs = extract_log_lines_for_block(log_file, block_number, 
                                           is_first_run=is_first_run, 
                                           boundary_time=boundary_time)
        
        if not logs:
            return None
        
        # Parse the logs to extract data
        block_data = {
            'block_number': block_number,
            'block_hash': 'N/A',
            'processing_time': 'N/A',
            'elapsed_time': 'N/A',
            'root_elapsed': 'N/A'
        }
        
        for log in logs:
            # Extract block hash from either the root calculation or block added log
            hash_match = re.search(r'hash=([0-9a-fx]+)', log)
            if hash_match:
                block_data['block_hash'] = hash_match.group(1)
            
            # Extract processing time from "All proofs processed"
            if 'All proofs processed' in log:
                time_match = re.search(r'total_time=Some\(([^)]+)\)', log)
                if time_match:
                    block_data['processing_time'] = time_match.group(1)
            
            # Extract elapsed time from "Block added"
            if 'Block added to canonical chain' in log:
                elapsed_match = re.search(r'elapsed=([^\s]+)', log)
                if elapsed_match:
                    block_data['elapsed_time'] = elapsed_match.group(1)
            
            # Extract root elapsed time from "Calculated state root"
            if 'Calculated state root' in log:
                root_match = re.search(r'root_elapsed=([^\s]+)', log)
                if root_match:
                    block_data['root_elapsed'] = root_match.group(1)
        
        return block_data

    def generate_block_comparison(self, block_number):
        """Generate a detailed comparison for a single block."""
        # Get data for this block from logs directly
        data_1 = self.get_block_data_from_logs(block_number, is_first_run=True)
        data_2 = self.get_block_data_from_logs(block_number, is_first_run=False)
        
        # Get log lines for display
        boundary_time = None
        if self.single_file and data_1 and data_2:
            # Calculate rough boundary between runs based on block data
            run1_logs = extract_log_lines_for_block(self.log_file_1, block_number, is_first_run=True, boundary_time=None)
            run2_logs = extract_log_lines_for_block(self.log_file_1, block_number, is_first_run=False, boundary_time=None)
            
            if run1_logs and run2_logs:
                # Extract timestamps from first logs in each set
                time1_match = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)', run1_logs[0])
                time2_match = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)', run2_logs[0])
                
                if time1_match and time2_match:
                    time1 = datetime.fromisoformat(time1_match.group(1).replace('Z', ''))
                    time2 = datetime.fromisoformat(time2_match.group(1).replace('Z', ''))
                    boundary_time = time1 + (time2 - time1) / 2
        
        # Get log lines for this block for display
        if self.single_file:
            log_lines_1 = extract_log_lines_for_block(self.log_file_1, block_number, 
                                                     is_first_run=True, boundary_time=boundary_time)
            log_lines_2 = extract_log_lines_for_block(self.log_file_1, block_number, 
                                                     is_first_run=False, boundary_time=boundary_time)
        else:
            log_lines_1 = extract_log_lines_for_block(self.log_file_1, block_number)
            log_lines_2 = extract_log_lines_for_block(self.log_file_2, block_number)
        
        if not data_1 or not data_2:
            return {
                'block_number': block_number,
                'error': f"Block {block_number} not found in both runs",
                'log_lines_1': log_lines_1,
                'log_lines_2': log_lines_2
            }, self.generate_empty_chart(f"Block {block_number} not found in both runs")
        
        # Prepare data for display
        block_data = {
            'block_number': block_number,
            'run1': {
                'hash': data_1.get('block_hash', 'N/A'),
                'processing_time': data_1.get('processing_time', 'N/A'),
                'elapsed_time': data_1.get('elapsed_time', 'N/A'),
                'root_elapsed': data_1.get('root_elapsed', 'N/A')
            },
            'run2': {
                'hash': data_2.get('block_hash', 'N/A'),
                'processing_time': data_2.get('processing_time', 'N/A'),
                'elapsed_time': data_2.get('elapsed_time', 'N/A'),
                'root_elapsed': data_2.get('root_elapsed', 'N/A')
            },
            'log_lines_1': log_lines_1,
            'log_lines_2': log_lines_2
        }
        
        # Calculate percentage differences
        try:
            proc_time_1 = self.extract_ms_value(data_1.get('processing_time', 'N/A'))
            proc_time_2 = self.extract_ms_value(data_2.get('processing_time', 'N/A'))
            elapsed_time_1 = self.extract_ms_value(data_1.get('elapsed_time', 'N/A'))
            elapsed_time_2 = self.extract_ms_value(data_2.get('elapsed_time', 'N/A'))
            root_time_1 = self.extract_ms_value(data_1.get('root_elapsed', 'N/A'))
            root_time_2 = self.extract_ms_value(data_2.get('root_elapsed', 'N/A'))
            
            if proc_time_1 and proc_time_2:
                block_data['proc_diff'] = ((proc_time_1 - proc_time_2) / proc_time_1) * 100
            if elapsed_time_1 and elapsed_time_2:
                block_data['elapsed_diff'] = ((elapsed_time_1 - elapsed_time_2) / elapsed_time_1) * 100
            if root_time_1 and root_time_2:
                block_data['root_diff'] = ((root_time_1 - root_time_2) / root_time_1) * 100
        except (ValueError, TypeError, ZeroDivisionError):
            pass
        
        # Create bar chart comparing the two runs
        fig, ax = plt.subplots(figsize=(10, 6))
        
        metrics = ['Processing Time', 'Block Elapsed Time', 'Root Calculation Time']
        run1_values = [
            self.extract_ms_value(data_1.get('processing_time', '0')) or 0,
            self.extract_ms_value(data_1.get('elapsed_time', '0')) or 0,
            self.extract_ms_value(data_1.get('root_elapsed', '0')) or 0
        ]
        run2_values = [
            self.extract_ms_value(data_2.get('processing_time', '0')) or 0,
            self.extract_ms_value(data_2.get('elapsed_time', '0')) or 0,
            self.extract_ms_value(data_2.get('root_elapsed', '0')) or 0
        ]
        
        x = np.arange(len(metrics))
        width = 0.35
        
        bars1 = ax.bar(x - width/2, run1_values, width, label='Run 1', color='skyblue')
        bars2 = ax.bar(x + width/2, run2_values, width, label='Run 2', color='salmon')
        
        # Add data labels and improvement percentages
        for i in range(len(metrics)):
            if run1_values[i] > 0 and run2_values[i] > 0:
                improvement = (run1_values[i] - run2_values[i]) / run1_values[i] * 100
                color = 'green' if improvement > 0 else 'red'
                sign = '+' if improvement < 0 else '' # + means worse in run 2, no sign means better
                
                # Label for run 1
                ax.annotate(f"{run1_values[i]:.2f}ms", 
                           xy=(i - width/2, run1_values[i]),
                           xytext=(0, 3),
                           textcoords="offset points",
                           ha='center', va='bottom')
                
                # Label for run 2
                ax.annotate(f"{run2_values[i]:.2f}ms\n({sign}{improvement:.1f}%)", 
                           xy=(i + width/2, run2_values[i]),
                           xytext=(0, 3),
                           textcoords="offset points",
                           ha='center', va='bottom', color=color)
        
        ax.set_ylabel('Time (ms)')
        ax.set_title(f'Block {block_number} Performance Comparison')
        ax.set_xticks(x)
        ax.set_xticklabels(metrics)
        ax.legend()
        
        plt.tight_layout()
        
        # Convert plot to base64 image
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        plt.close(fig)
        buf.seek(0)
        img_str = base64.b64encode(buf.read()).decode('utf-8')
        
        return block_data, img_str

    def extract_ms_value(self, time_str):
        """Extract millisecond value from time string like '24.737359ms'."""
        if time_str == 'N/A' or not time_str:
            return None

        # Try to convert "24.737359ms" to float
        try:
            # Remove the 'ms' suffix if present
            if time_str.endswith('ms'):
                time_str = time_str[:-2]
            return float(time_str)
        except (ValueError, TypeError):
            return None

    def generate_empty_chart(self, message):
        """Generate an empty chart with a message."""
        plt.figure(figsize=(10, 6))
        plt.text(0.5, 0.5, message, ha='center', va='center', fontsize=14)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        plt.close()
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')

    def generate_overview_html(self, img_data):
        """Generate HTML for the overview page."""
        # Get all common block numbers for the dropdown
        block_options = ""
        for block_number in self.block_numbers_cache:
            block_options += f'<option value="{block_number}">{block_number}</option>'

        # Set up description based on single or dual file mode
        if self.single_file:
            file_description = f'''
            <p><strong>Log file:</strong> {self.log_file_1}</p>
            <p>Two benchmark runs detected in the same file.</p>
            '''
        else:
            file_description = f'''
            <p><strong>Run 1:</strong> {self.log_file_1}</p>
            <p><strong>Run 2:</strong> {self.log_file_2}</p>
            '''

        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Log Performance Comparison</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1, h2 {{ color: #333; }}
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
                .error {{ color: #d32f2f; margin-top: 5px; display: none; }}
            </style>
            <script>
                function viewBlock() {{
                    const blockSelect = document.getElementById('blockSelect');
                    if (blockSelect.value !== '') {{
                        window.location.href = '/?block=' + blockSelect.value;
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
                            if (data.block_number) {{
                                window.location.href = '/?block=' + data.block_number;
                            }} else {{
                                errorMsg.textContent = 'Block number not found.';
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
                <h1>Log Performance Comparison</h1>

                <div class="controls">
                    <div class="description">
                        <p>This tool compares block processing performance between two benchmark runs.</p>
                    </div>

                    <div>
                        <label for="logInput">Paste a log line to find its block:</label>
                        <textarea id="logInput" placeholder="Paste a log line containing a block number..."></textarea>
                        <div id="errorMsg" class="error"></div>
                        <button onclick="findBlockFromLog()">Find Block</button>
                    </div>

                    <div style="margin-top: 20px;">
                        <label for="blockSelect">Or select a block directly:</label>
                        <div style="display: flex; align-items: center;">
                            <select id="blockSelect" style="flex-grow: 1;">
                                <option value="">-- Select Block --</option>
                                {block_options}
                            </select>
                            <button onclick="viewBlock()">View Block</button>
                        </div>
                    </div>
                </div>

                <h2>Performance Overview</h2>
                <div>
                    <img src="data:image/png;base64,{img_data}" class="graph" alt="Performance Comparison Graph">
                </div>

                <div class="info">
                    <p>Comparing logs:</p>
                    {file_description}
                </div>
            </div>
        </body>
        </html>
        '''

    def generate_block_comparison_html(self, block_data, img_data):
        """Generate HTML for a single block comparison page."""
        # Get all common block numbers for the dropdown
        block_options = ""
        for block_number in self.block_numbers_cache:
            selected = "selected" if block_number == block_data['block_number'] else ""
            block_options += f'<option value="{block_number}" {selected}>{block_number}</option>'
        
        # Format differences with colors
        proc_diff_html = ""
        elapsed_diff_html = ""
        root_diff_html = ""
        
        if 'proc_diff' in block_data:
            color = "green" if block_data['proc_diff'] > 0 else "red"
            sign = "" if block_data['proc_diff'] > 0 else "+"
            proc_diff_html = f'<span style="color: {color};">({sign}{block_data["proc_diff"]:.2f}%)</span>'
            
        if 'elapsed_diff' in block_data:
            color = "green" if block_data['elapsed_diff'] > 0 else "red"
            sign = "" if block_data['elapsed_diff'] > 0 else "+"
            elapsed_diff_html = f'<span style="color: {color};">({sign}{block_data["elapsed_diff"]:.2f}%)</span>'
            
        if 'root_diff' in block_data:
            color = "green" if block_data['root_diff'] > 0 else "red"
            sign = "" if block_data['root_diff'] > 0 else "+"
            root_diff_html = f'<span style="color: {color};">({sign}{block_data["root_diff"]:.2f}%)</span>'
        
        # Check if there's an error
        error_html = ""
        if 'error' in block_data:
            error_html = f'<div class="error" style="display: block;">{block_data["error"]}</div>'
        
        # Format log lines
        log_lines_1_html = "<pre class='log-lines'>" + "\n".join(block_data.get('log_lines_1', [])) + "</pre>"
        log_lines_2_html = "<pre class='log-lines'>" + "\n".join(block_data.get('log_lines_2', [])) + "</pre>"
        
        # Set up description based on single or dual file mode
        if self.single_file:
            file_description = f'''
            <p><strong>Log file:</strong> {self.log_file_1}</p>
            <p>Two benchmark runs detected in the same file.</p>
            '''
        else:
            file_description = f'''
            <p><strong>Run 1:</strong> {self.log_file_1}</p>
            <p><strong>Run 2:</strong> {self.log_file_2}</p>
            '''
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Block {block_data['block_number']} Comparison</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1, h2, h3 {{ color: #333; }}
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
                .error {{ color: #d32f2f; margin-top: 5px; display: none; }}
                .comparison {{ margin: 20px 0; }}
                .comparison table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
                .comparison th, .comparison td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
                .comparison th {{ background-color: #f8f9fa; }}
                .back-link {{ margin-bottom: 20px; }}
                .back-link a {{ color: #4CAF50; text-decoration: none; }}
                .back-link a:hover {{ text-decoration: underline; }}
                .log-section {{ margin-top: 30px; }}
                .log-lines {{ background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto; font-size: 12px; max-height: 300px; overflow-y: auto; }}
                .tabs {{ display: flex; margin-bottom: 10px; }}
                .tab {{ padding: 8px 16px; cursor: pointer; background-color: #f8f9fa; border: 1px solid #ddd; }}
                .tab.active {{ background-color: #4CAF50; color: white; }}
                .tab-content {{ display: none; }}
                .tab-content.active {{ display: block; }}
            </style>
            <script>
                function viewBlock() {{
                    const blockSelect = document.getElementById('blockSelect');
                    if (blockSelect.value !== '') {{
                        window.location.href = '/?block=' + blockSelect.value;
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
                            if (data.block_number) {{
                                window.location.href = '/?block=' + data.block_number;
                            }} else {{
                                errorMsg.textContent = 'Block number not found.';
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
                
                function switchTab(tabName) {{
                    // Hide all tab content
                    const tabContents = document.getElementsByClassName('tab-content');
                    for (let i = 0; i < tabContents.length; i++) {{
                        tabContents[i].classList.remove('active');
                    }}
                    
                    // Deactivate all tabs
                    const tabs = document.getElementsByClassName('tab');
                    for (let i = 0; i < tabs.length; i++) {{
                        tabs[i].classList.remove('active');
                    }}
                    
                    // Activate the selected tab and content
                    document.getElementById(tabName).classList.add('active');
                    document.getElementById(tabName + '-tab').classList.add('active');
                }}
            </script>
        </head>
        <body>
            <div class="container">
                <h1>Block {block_data['block_number']} Performance Comparison</h1>
                
                <div class="back-link">
                    <a href="/">&laquo; Back to Overview</a>
                </div>
                
                {error_html}
                
                <div class="controls">
                    <div style="display: flex; align-items: flex-start; gap: 20px;">
                        <div style="flex: 1;">
                            <label for="logInput">Paste a log line to find its block:</label>
                            <textarea id="logInput" placeholder="Paste a log line containing a block number..."></textarea>
                            <div id="errorMsg" class="error"></div>
                            <button onclick="findBlockFromLog()">Find Block</button>
                        </div>
                        
                        <div style="flex: 1;">
                            <label for="blockSelect">Or select a block directly:</label>
                            <div style="display: flex; align-items: center;">
                                <select id="blockSelect" style="flex-grow: 1;">
                                    <option value="">-- Select Block --</option>
                                    {block_options}
                                </select>
                                <button onclick="viewBlock()">View Block</button>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="comparison">
                    <h2>Block Details</h2>
                    <table>
                        <tr>
                            <th>Metric</th>
                            <th>Run 1</th>
                            <th>Run 2</th>
                            <th>Difference</th>
                        </tr>
                        <tr>
                            <td>Block Hash</td>
                            <td>{block_data['run1']['hash']}</td>
                            <td>{block_data['run2']['hash']}</td>
                            <td>{'-' if block_data['run1']['hash'] == block_data['run2']['hash'] else 'Different hashes'}</td>
                        </tr>
                        <tr>
                            <td>Processing Time</td>
                            <td>{block_data['run1']['processing_time']}</td>
                            <td>{block_data['run2']['processing_time']}</td>
                            <td>{proc_diff_html}</td>
                        </tr>
                        <tr>
                            <td>Block Elapsed Time</td>
                            <td>{block_data['run1']['elapsed_time']}</td>
                            <td>{block_data['run2']['elapsed_time']}</td>
                            <td>{elapsed_diff_html}</td>
                        </tr>
                        <tr>
                            <td>Root Calculation Time</td>
                            <td>{block_data['run1']['root_elapsed']}</td>
                            <td>{block_data['run2']['root_elapsed']}</td>
                            <td>{root_diff_html}</td>
                        </tr>
                    </table>
                </div>
                
                <div>
                    <img src="data:image/png;base64,{img_data}" class="graph" alt="Performance Comparison Graph">
                </div>
                
                <div class="log-section">
                    <h2>Block Log Lines</h2>
                    <div class="tabs">
                        <div id="run1-tab" class="tab active" onclick="switchTab('run1')">Run 1</div>
                        <div id="run2-tab" class="tab" onclick="switchTab('run2')">Run 2</div>
                    </div>
                    <div id="run1" class="tab-content active">
                        <h3>Run 1 Logs</h3>
                        {log_lines_1_html}
                    </div>
                    <div id="run2" class="tab-content">
                        <h3>Run 2 Logs</h3>
                        {log_lines_2_html}
                    </div>
                </div>
                
                <div class="info">
                    <p>Comparing logs:</p>
                    {file_description}
                </div>
            </div>
            
            <script>
                // Initialize tabs
                switchTab('run1');
            </script>
        </body>
        </html>
        '''

def extract_block_number_from_log(log_line):
    """Extract block number from a log line."""
    # Look for "Block added to canonical chain number=X" pattern
    chain_pattern = r'Block added to canonical chain number=(\d+)'
    match = re.search(chain_pattern, log_line)
    if match:
        return match.group(1)

    # Look for "Calculated state root ... block=NumHash { number: X" pattern
    root_pattern = r'Calculated state root .+? block=NumHash { number: (\d+)'
    match = re.search(root_pattern, log_line)
    if match:
        return match.group(1)

    # Look for any block number format
    block_pattern = r'block(?:\s+|\s*=\s*|\s+number\s*=\s*)(\d+)'
    match = re.search(block_pattern, log_line, re.IGNORECASE)
    if match:
        return match.group(1)

    return None

def extract_block_metadata_from_single_file(log_file_path):
    """
    Extract block metadata from a single log file containing both runs.
    Returns two separate metadata dictionaries.
    """
    # First, extract all metadata as usual
    all_metadata = extract_block_metadata(log_file_path)

    # Then split the metadata into two runs based on block occurrences
    # We'll identify blocks that appear twice and separate them
    block_occurrences = {}

    # Count block occurrences
    for timestamp, metadata in all_metadata.items():
        if 'block_number' in metadata:
            block_num = metadata['block_number']
            if block_num not in block_occurrences:
                block_occurrences[block_num] = []
            block_occurrences[block_num].append((timestamp, metadata))

    # Create separate dictionaries for each run
    run1_metadata = {}
    run2_metadata = {}

    # For each block that appears twice, split into two runs
    for block_num, occurrences in block_occurrences.items():
        # Sort by timestamp
        occurrences.sort(key=lambda x: x[0])

        # If we have two occurrences of the same block, they represent two runs
        if len(occurrences) >= 2:
            # First occurrence goes to run 1
            timestamp1, metadata1 = occurrences[0]
            run1_metadata[timestamp1] = metadata1

            # Second occurrence goes to run 2
            timestamp2, metadata2 = occurrences[1]
            run2_metadata[timestamp2] = metadata2
        elif len(occurrences) == 1:
            # If a block appears only once, add it to both runs
            # This ensures we don't miss any blocks in either run
            timestamp, metadata = occurrences[0]
            run1_metadata[timestamp] = metadata.copy()
            run2_metadata[timestamp + "_dup"] = metadata.copy()  # Add a unique key

    return run1_metadata, run2_metadata

def extract_block_metadata(log_file_path):
    """
    Extract block metadata from the log file, including block numbers, hashes,
    elapsed times, and root calculation times.
    """
    block_metadata = {}
    blocks_data = {}  # Data indexed by block number rather than timestamp
    
    # Regular expressions for important log entries
    block_added_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+INFO reth_node_events::node: Block added to canonical chain number=(\d+) hash=(0x[a-f0-9]+).+?elapsed=(\S+)'
    root_elapsed_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+DEBUG engine::tree: Calculated state root root_elapsed=(\S+) block=NumHash { number: (\d+), hash: (0x[a-f0-9]+)'
    proofs_processed_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+DEBUG engine::root: All proofs processed, ending calculation.+?total_time=Some\((\S+)\)'
    
    # Extract data from log file
    with open(log_file_path, 'r') as file:
        file_content = file.read()
        
        # First collect all block info entries
        for match in re.finditer(block_added_pattern, file_content):
            timestamp = match.group(1)
            block_number = match.group(2)
            block_hash = match.group(3)
            elapsed_time = match.group(4)
            
            if block_number not in blocks_data:
                blocks_data[block_number] = []
            
            blocks_data[block_number].append({
                'timestamp': timestamp,
                'block_hash': block_hash,
                'elapsed_time': elapsed_time,
                'block_number': block_number
            })
        
        # Add root calculation times
        for match in re.finditer(root_elapsed_pattern, file_content):
            timestamp = match.group(1)
            root_elapsed = match.group(2)
            block_number = match.group(3)
            block_hash = match.group(4)
            
            if block_number in blocks_data:
                # Find entry closest to this timestamp
                closest_entry = None
                min_diff = float('inf')
                
                for entry in blocks_data[block_number]:
                    entry_time = datetime.fromisoformat(entry['timestamp'].replace('Z', ''))
                    current_time = datetime.fromisoformat(timestamp.replace('Z', ''))
                    diff = abs((current_time - entry_time).total_seconds())
                    
                    if diff < min_diff:
                        min_diff = diff
                        closest_entry = entry
                
                if closest_entry:
                    closest_entry['root_elapsed'] = root_elapsed
        
        # Finally process all proof time entries
        proof_times = []
        for match in re.finditer(proofs_processed_pattern, file_content):
            timestamp = match.group(1)
            total_time = match.group(2)
            proof_times.append((timestamp, total_time))
        
        # Sort all proof times by timestamp
        proof_times.sort(key=lambda x: x[0])
        
        # For each block, associate with the nearest "All proofs processed" entry
        for block_number, entries in blocks_data.items():
            for entry in entries:
                entry_time = datetime.fromisoformat(entry['timestamp'].replace('Z', ''))
                
                # Find the closest proof processing time after this block
                for proof_timestamp, total_time in proof_times:
                    proof_time = datetime.fromisoformat(proof_timestamp.replace('Z', ''))
                    
                    # Look for proof processing that happened soon after this block
                    time_diff = (proof_time - entry_time).total_seconds()
                    if 0 <= time_diff <= 10:  # Within 10 seconds after block
                        entry['total_time'] = total_time
                        break
    
    # Convert to the expected format
    for block_number, entries in blocks_data.items():
        for entry in entries:
            block_metadata[entry['timestamp']] = entry
    
    print(f"Extracted metadata for {len(block_metadata)} log entries from {log_file_path}")
    print(f"Found data for {len(blocks_data)} unique blocks")
    
    return block_metadata

def extract_log_lines_for_block(log_file_path, block_number, is_first_run=True, boundary_time=None):
    """
    Extract only the specified log lines related to a specific block number.
    
    For a given block, we want:
    1. The "Block added to canonical chain" log for this block
    2. The "All proofs processed" log that occurred right before this block was added
    3. The "Calculated state root" log for this block
    
    Parameters:
    - log_file_path: Path to the log file
    - block_number: Block number to extract logs for
    - is_first_run: If True, gets the first occurrence of the block, otherwise the second
    - boundary_time: For single-file mode, a timestamp to separate runs (anything before this is Run 1)
    """
    # First collect all lines with timestamps
    all_block_added_lines = []
    all_root_calculated_lines = []
    all_proofs_processed_lines = []
    
    # Regular expressions for the specific logs
    block_added_pattern = rf'INFO reth_node_events::node: Block added to canonical chain number={block_number}\b'
    root_calculated_pattern = rf'DEBUG engine::tree: Calculated state root.+?block=NumHash {{ number: {block_number}\b'
    proofs_processed_pattern = r'DEBUG engine::root: All proofs processed, ending calculation.+?total_time=Some'
    
    # Read all relevant lines with timestamps
    with open(log_file_path, 'r') as file:
        for line in file:
            timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)', line)
            if not timestamp_match:
                continue
                
            timestamp = timestamp_match.group(1)
            timestamp_dt = datetime.fromisoformat(timestamp.replace('Z', ''))
            
            # Check for Block added line
            if re.search(block_added_pattern, line):
                all_block_added_lines.append((timestamp, line.strip()))
            
            # Check for Calculated state root line
            elif re.search(root_calculated_pattern, line):
                all_root_calculated_lines.append((timestamp, line.strip()))
            
            # Keep track of all proofs processed lines
            elif re.search(proofs_processed_pattern, line):
                all_proofs_processed_lines.append((timestamp, line.strip()))
    
    # Sort all lines by timestamp
    all_block_added_lines.sort(key=lambda x: x[0])
    all_root_calculated_lines.sort(key=lambda x: x[0])
    all_proofs_processed_lines.sort(key=lambda x: x[0])
    
    # Handle single-file mode with two runs of the same block
    if len(all_block_added_lines) >= 2 and boundary_time is not None:
        # Split into runs
        run1_lines = []
        run2_lines = []
        
        for timestamp, line in all_block_added_lines:
            timestamp_dt = datetime.fromisoformat(timestamp.replace('Z', ''))
            if timestamp_dt < boundary_time:
                run1_lines.append((timestamp, line))
            else:
                run2_lines.append((timestamp, line))
        
        # Select the right run
        selected_lines = run1_lines if is_first_run else run2_lines
        
        # If we found an entry for this run
        if selected_lines:
            block_added_time, block_added_line = selected_lines[0]
        else:
            return []
    elif all_block_added_lines:
        # For normal mode or only one occurrence
        block_index = 0 if is_first_run or len(all_block_added_lines) == 1 else 1
        if block_index < len(all_block_added_lines):
            block_added_time, block_added_line = all_block_added_lines[block_index]
        else:
            return []
    else:
        # No block added logs found
        return []
    
    # Convert to datetime
    block_added_dt = datetime.fromisoformat(block_added_time.replace('Z', ''))
    
    # Find the "All proofs processed" log that occurred right before the block was added
    proofs_processed_line = None
    
    for timestamp, line in all_proofs_processed_lines:
        line_dt = datetime.fromisoformat(timestamp.replace('Z', ''))
        # Look for proofs processed log that happened before the block was added
        # But not too far before (within 2 seconds is a reasonable window)
        time_diff = (block_added_dt - line_dt).total_seconds()
        if 0 <= time_diff <= 2:
            proofs_processed_line = line
            break
    
    # Find the "Calculated state root" log for this block
    # It should be close to the block added time
    root_calculated_line = None
    
    for timestamp, line in all_root_calculated_lines:
        line_dt = datetime.fromisoformat(timestamp.replace('Z', ''))
        # The state root calculation typically happens shortly before the block is added
        time_diff = abs((block_added_dt - line_dt).total_seconds())
        if time_diff <= 5:  # Within 5 seconds
            root_calculated_line = line
            break
    
    # Collect the final set of logs
    result_logs = []
    
    # First add the proofs processed line if found
    if proofs_processed_line:
        result_logs.append(proofs_processed_line)
    
    # Then add calculated state root log if found
    if root_calculated_line:
        result_logs.append(root_calculated_line)
    
    # Finally add the block added line
    result_logs.append(block_added_line)
    
    return result_logs

def detect_single_or_dual_file(log_file_1, log_file_2=None):
    """Detect if we should use single-file mode or dual-file mode."""
    if log_file_2 is None or log_file_1 == log_file_2:
        # Check if the single file has duplicate block entries
        all_metadata = extract_block_metadata(log_file_1)

        # Count block occurrences
        block_occurrences = {}
        for _, metadata in all_metadata.items():
            if 'block_number' in metadata:
                block_num = metadata['block_number']
                block_occurrences[block_num] = block_occurrences.get(block_num, 0) + 1

        # If we have blocks that appear twice, it's a single file with two runs
        duplicate_blocks = [block for block, count in block_occurrences.items() if count > 1]
        if duplicate_blocks:
            print(f"Detected single file with {len(duplicate_blocks)} duplicate blocks - using single-file mode")
            return True, log_file_1, None
        else:
            print("No duplicate blocks found - please provide two separate log files for comparison")
            return False, log_file_1, log_file_2
    else:
        # Two different files provided
        return False, log_file_1, log_file_2

def start_server(log_file_1, log_file_2=None, port=8000):
    # Detect if we should use single-file mode or dual-file mode
    single_file_mode, file_1, file_2 = detect_single_or_dual_file(log_file_1, log_file_2)

    # Create a custom handler with access to the log files
    handler = lambda *args, **kwargs: LogComparisonHandler(
        *args,
        log_file_1=file_1,
        log_file_2=file_2,
        single_file=single_file_mode,
        **kwargs
    )

    server = HTTPServer(('localhost', port), handler)
    print(f"Server started at http://localhost:{port}")
    if single_file_mode:
        print(f"Using single-file mode with log file: {file_1}")
    else:
        print(f"Comparing log files:")
        print(f"  Run 1: {file_1}")
        print(f"  Run 2: {file_2}")
    print("Press Ctrl+C to stop the server")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped.")
        server.server_close()

def main():
    parser = argparse.ArgumentParser(description='Compare block processing performance between benchmark runs')
    parser.add_argument('log_file_1', help='Path to the log file (single file with both runs, or first run)')
    parser.add_argument('log_file_2', nargs='?', default=None,
                        help='Path to the second log file (optional, for separate run files)')
    parser.add_argument('-p', '--port', type=int, default=8000, help='Port to run the HTTP server on (default: 8000)')

    args = parser.parse_args()

    if not os.path.exists(args.log_file_1):
        print(f"Error: Log file '{args.log_file_1}' not found.")
        return

    if args.log_file_2 and not os.path.exists(args.log_file_2):
        print(f"Error: Log file '{args.log_file_2}' not found.")
        return

    # Start the HTTP server
    start_server(args.log_file_1, args.log_file_2, args.port)

if __name__ == "__main__":
    main()
