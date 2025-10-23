import pandas as pd
import numpy as np
from utils import setup_logger

logger = setup_logger('earnings_analyzer')

class EarningsAnalyzer:
    """Analyzes C (Current Earnings) and A (Annual Earnings) criteria."""
    
    def __init__(self, data_manager):
        self.data_manager = data_manager
    
    def check_c_criterion(self, ticker, financial_data):
        """
        C - Current Earnings: YoY EPS growth ≥ 20% for last two quarters.
        
        Returns: (pass: bool, details: dict)
        """
        if financial_data is None or financial_data.empty:
            return False, {'reason': 'No financial data available'}
        
        try:
            # Filter for EPS data
            eps_data = financial_data[
                (financial_data['account_nm'].str.contains('주당순이익|EPS', na=False)) &
                (financial_data['sj_div'] == 'CFS')  # Consolidated Financial Statements
            ].copy()
            
            if eps_data.empty:
                return False, {'reason': 'No EPS data found'}
            
            # Sort by date
            eps_data = eps_data.sort_values('rcept_no', ascending=False)
            
            # Get last 4 quarters to calculate YoY growth
            if len(eps_data) < 4:
                return False, {'reason': 'Insufficient quarterly data'}
            
            # Extract EPS values
            recent_quarters = []
            for idx, row in eps_data.head(4).iterrows():
                try:
                    eps_value = float(str(row['thstrm_amount']).replace(',', ''))
                    recent_quarters.append(eps_value)
                except:
                    continue
            
            if len(recent_quarters) < 4:
                return False, {'reason': 'Unable to parse EPS values'}
            
            # Calculate YoY growth for last two quarters
            q1_growth = ((recent_quarters[0] - recent_quarters[2]) / abs(recent_quarters[2]) * 100) if recent_quarters[2] != 0 else 0
            q2_growth = ((recent_quarters[1] - recent_quarters[3]) / abs(recent_quarters[3]) * 100) if recent_quarters[3] != 0 else 0
            
            passes = q1_growth >= 20 and q2_growth >= 20
            
            return passes, {
                'q1_yoy_growth': round(q1_growth, 2),
                'q2_yoy_growth': round(q2_growth, 2)
            }
            
        except Exception as e:
            logger.debug(f"Error checking C criterion for {ticker}: {e}")
            return False, {'reason': str(e)}
    
    def check_a_criterion(self, ticker, financial_data):
        """
        A - Annual Earnings: 3-year EPS CAGR ≥ 20% and latest ROE ≥ 15%.
        
        Returns: (pass: bool, details: dict)
        """
        if financial_data is None or financial_data.empty:
            return False, {'reason': 'No financial data available'}
        
        try:
            # Get annual EPS data
            eps_data = financial_data[
                (financial_data['account_nm'].str.contains('주당순이익|EPS', na=False)) &
                (financial_data['sj_div'] == 'CFS') &
                (financial_data['reprt_code'] == '11011')  # Annual report
            ].copy()
            
            # Get ROE data
            roe_data = financial_data[
                (financial_data['account_nm'].str.contains('자기자본이익률|ROE', na=False)) &
                (financial_data['reprt_code'] == '11011')
            ].copy()
            
            # Check EPS CAGR
            eps_pass = False
            eps_cagr = 0
            
            if len(eps_data) >= 4:
                eps_data = eps_data.sort_values('bsns_year', ascending=False)
                try:
                    current_eps = float(str(eps_data.iloc[0]['thstrm_amount']).replace(',', ''))
                    three_years_ago_eps = float(str(eps_data.iloc[3]['thstrm_amount']).replace(',', ''))
                    
                    if three_years_ago_eps > 0:
                        eps_cagr = (((current_eps / three_years_ago_eps) ** (1/3)) - 1) * 100
                        eps_pass = eps_cagr >= 20
                except:
                    pass
            
            # Check ROE
            roe_pass = False
            latest_roe = 0
            
            if not roe_data.empty:
                roe_data = roe_data.sort_values('bsns_year', ascending=False)
                try:
                    latest_roe = float(str(roe_data.iloc[0]['thstrm_amount']).replace(',', ''))
                    roe_pass = latest_roe >= 15
                except:
                    pass
            
            passes = eps_pass and roe_pass
            
            return passes, {
                'eps_cagr_3y': round(eps_cagr, 2),
                'latest_roe': round(latest_roe, 2),
                'eps_pass': eps_pass,
                'roe_pass': roe_pass
            }
            
        except Exception as e:
            logger.debug(f"Error checking A criterion for {ticker}: {e}")
            return False, {'reason': str(e)}
