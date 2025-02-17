from flask import Flask, request, jsonify, send_file, render_template
import pandas as pd
import logging
import os
from werkzeug.utils import secure_filename
import io
from datetime import datetime
import uuid

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 
app.config['TEMPLATES_AUTO_RELOAD'] = True

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('templates', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'xlsx', 'xls'}

def process_student_data(df):
    """Process student data and return SQL statements"""
    try:
        required_columns = {"Reg_no", "Roll_no", "Name", "Sec", "DOB"}
        if not required_columns.issubset(df.columns):
            missing_cols = required_columns - set(df.columns)
            raise ValueError(f"Missing required columns: {missing_cols}")

        total_students = len(df)
        logger.info(f"Total students in input file: {total_students}")

        sections = df.groupby("Sec")
        section_counts = sections.size().to_dict()
        
        distribution_info = {
            "total_students": total_students,
            "section_counts": section_counts,
            "set_distribution": {}
        }

        set_labels = ["A", "B", "C", "D", "E"]
        output_data = []

        for sec, group in sections:
            num_students = len(group)
            students_per_set = num_students // len(set_labels)
            remainder = num_students % len(set_labels)

            set_distribution = [students_per_set] * len(set_labels)
            for i in range(remainder):
                set_distribution[i] += 1

            distribution_info["set_distribution"][sec] = dict(zip(set_labels, set_distribution))

            logger.info(f"Section {sec} distribution plan:")
            for label, count in zip(set_labels, set_distribution):
                logger.info(f"Set {label}: {count} students")

            shuffled_students = group.sample(frac=1, random_state=42).reset_index(drop=True)
            start = 0
            for set_label, count in zip(set_labels, set_distribution):
                assigned_students = shuffled_students.iloc[start:start + count].copy()
                assigned_students["Set"] = set_label
                output_data.append(assigned_students)
                start += count

        final_df = pd.concat(output_data, ignore_index=True)

        def format_dob(dob):
            try:
                if pd.isna(dob):
                    return "NULL"
                dob_parsed = pd.to_datetime(dob)
                return f"STR_TO_DATE('{dob_parsed.strftime('%d-%m-%Y')}', '%d-%m-%Y')"
            except Exception as e:
                logger.error(f"Error processing DOB '{dob}': {e}")
                return "NULL"

        final_df["DOB"] = final_df["DOB"].apply(format_dob)

        table_name = "students"
        sql_statements = []

        for _, student in final_df.iterrows():
            sql = f"INSERT INTO {table_name} (Reg_no, Roll_no, Name, Sec, DOB, Set) VALUES " \
                  f"('{student['Reg_no']}', '{student['Roll_no']}', '{student['Name']}', " \
                  f"'{student['Sec']}', {student['DOB']}, '{student['Set']}');"
            sql_statements.append(sql)

        return final_df, sql_statements, distribution_info

    except Exception as e:
        logger.error(f"Error in process_student_data: {str(e)}")
        raise

@app.route('/')
def index():
    """Serve the main page"""
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Error serving index page: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and process student data"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Please upload .xlsx or .xls file'}), 400

        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4().hex)  # Create a unique ID
        safe_filename = f"{timestamp}_{unique_id}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)

        file.save(file_path)
        logger.info(f"File saved: {file_path}")

        df = pd.read_excel(file_path)
        
        final_df, sql_statements, distribution_info = process_student_data(df)
    
        sql_filename = f"insert_students_{timestamp}_{unique_id}.sql"
        sql_path = os.path.join(app.config['UPLOAD_FOLDER'], sql_filename)
        with open(sql_path, "w") as f:
            f.write("\n".join(sql_statements))
        
        logger.info(f"SQL file generated: {sql_path}")

        app.config['LATEST_SQL_FILE'] = sql_filename

        return jsonify({
            'message': 'File processed successfully',
            'distribution_info': distribution_info,
            'sql_file_generated': True,
            'total_statements': len(sql_statements)
        })

    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/sql')
def download_sql():
    """Download the latest generated SQL file"""
    try:
        sql_filename = app.config.get('LATEST_SQL_FILE')
        if not sql_filename:
            return jsonify({'error': 'No SQL file available. Please upload and process a file first'}), 404

        sql_path = os.path.join(app.config['UPLOAD_FOLDER'], sql_filename)
        if not os.path.exists(sql_path):
            return jsonify({'error': 'SQL file not found'}), 404

        return send_file(
            sql_path,
            mimetype='text/plain',
            as_attachment=True,
            download_name='insert_students.sql'
        )

    except Exception as e:
        logger.error(f"Error downloading SQL file: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/distribution/<section>')
def get_section_distribution(section):
    """Get distribution information for a specific section"""
    try:
        sql_filename = app.config.get('LATEST_SQL_FILE')
        if not sql_filename:
            return jsonify({'error': 'No processed data found'}), 404

        sql_path = os.path.join(app.config['UPLOAD_FOLDER'], sql_filename)
        if not os.path.exists(sql_path):
            return jsonify({'error': 'SQL file not found'}), 404

        with open(sql_path, 'r') as f:
            sql_content = f.read()
        
        set_distribution = {}
        for set_label in ["A", "B", "C", "D", "E"]:
            count = sql_content.count(f"'{section}', STR_TO_DATE")
            set_distribution[set_label] = count

        return jsonify({
            'section': section,
            'distribution': set_distribution
        })

    except Exception as e:
        logger.error(f"Error getting distribution for section {section}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors"""
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
