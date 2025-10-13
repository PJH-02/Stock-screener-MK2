import pandas as pd
from ..utils import setup_logger

logger = setup_logger('supply_analyzer')

class SupplyAnalyzer:
    """Analyzes S (Supply and Demand) criterion."""
    
    def check_s_criterion(self, ticker, ohlcv):
        """
        S - Supply and Demand: 5-day avg volume must be >2x or <0.3x the 50-day avg volume.
        
        Args:
            ticker: Stock ticker
            ohlcv: DataFrame with OHLCV data
        
        Returns: (pass: bool, details: dict)
        """
        if ohlcv is None or ohlcv.empty:
            return False, {'reason': 'No OHLCV data available'}
        
        try:
            if len(ohlcv) < 50:
                return False, {'reason': 'Insufficient volume data'}
            
            # Calculate average volumes
            vol_5d = ohlcv['거래량'].tail(5).mean()
            vol_50d = ohlcv['거래량'].tail(50).mean()
            
            if vol_50d == 0:
                return False, {'reason': 'Zero 50-day average volume'}
            
            # Calculate ratio
            volume_ratio = vol_5d / vol_50d
            
            # Check if ratio is >2 or <0.3
            passes = (volume_ratio > 2.0) or (volume_ratio < 0.3)
            
            return passes, {
                'vol_5d_avg': int(vol_5d),
                'vol_50d_avg': int(vol_50d),
                'volume_ratio': round(volume_ratio, 3),
                'signal': 'High' if volume_ratio > 2.0 else 'Low' if volume_ratio < 0.3 else 'Normal'
            }
            
        except Exception as e:
            logger.debug(f"Error checking S criterion for {ticker}: {e}")
            return False, {'reason': str(e)}