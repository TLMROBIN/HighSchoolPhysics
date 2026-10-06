"""Read response tables without executing workbook formulas or macros."""
import base64
import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET
from .errors import InvalidRequest

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read_file(payload):
    try:
        raw = base64.b64decode(payload.get('file_data', ''), validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidRequest('文件编码无效') from exc
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise InvalidRequest('表格文件需小于 8MB')
    name = str(payload.get('file_name', '')).lower()
    if name.endswith('.csv'):
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                pass
        raise InvalidRequest('CSV 编码无法识别，请另存为 UTF-8')
    if not name.endswith('.xlsx'):
        raise InvalidRequest('请选择 CSV 或 XLSX 文件；旧版 XLS 请另存为 XLSX')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 40 * 1024 * 1024:
                raise InvalidRequest('Excel 解压后内容过大')
            strings = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                strings = [''.join(t.text or '' for t in item.findall('.//s:t', NS))
                           for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
            book = ET.fromstring(archive.read('xl/workbook.xml'))
            sheet = book.find('s:sheets/s:sheet', NS)
            relation = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
            target = next(item.get('Target') for item in relations if item.get('Id') == relation)
            sheet_path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            root = ET.fromstring(archive.read(sheet_path))
            rows = []
            for row in root.findall('s:sheetData/s:row', NS):
                values = []
                for cell in row.findall('s:c', NS):
                    letters = re.match(r'[A-Z]+', cell.get('r', '')).group()
                    column = 0
                    for letter in letters:
                        column = column * 26 + ord(letter) - 64
                    if column > 100:
                        raise InvalidRequest('表格列数过多')
                    while len(values) < column:
                        values.append('')
                    if cell.find('s:f', NS) is not None:
                        raise InvalidRequest('导入表格含公式，请复制并粘贴为数值后导入')
                    value = cell.findtext('s:v', default='', namespaces=NS)
                    if cell.get('t') == 's':
                        value = strings[int(value)]
                    elif cell.get('t') == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                    values[column - 1] = value
                if any(values):
                    rows.append(values)
                if len(rows) > 30001:
                    raise InvalidRequest('表格最多支持 30000 条作答')
            width = len(rows[0]) if rows else 0
            output = io.StringIO()
            writer = csv.writer(output)
            for row in rows:
                writer.writerow(row + [''] * max(0, width - len(row)))
            return output.getvalue()
    except InvalidRequest:
        raise
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, ValueError, AttributeError, StopIteration) as exc:
        raise InvalidRequest('Excel 文件无法读取，请检查文件或另存为 CSV') from exc
