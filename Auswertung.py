# -*- coding: utf-8 -*-
"""
Created on Mon Apr 29 11:21:04 2024

@author: Besitzer
"""
import copy

import sys
import os
import pickle
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import tzlocal
import itertools

from matplotlib.dates import DateFormatter
import matplotlib.pyplot as plt
#plt.rcParams['text.usetex'] = True

from scipy.signal import  savgol_filter
from scipy import interpolate, integrate, optimize, stats
if os.getlogin() == 'joeac':
    sys.path.append("C:/git/airfoilwinggeometry")
else:
    sys.path.append("D:/Python_Codes/Uebung1/modules/airfoilwinggeometry")
from airfoilwinggeometry.AirfoilPackage import AirfoilTools as at





def read_AOA_file(filename, sigma_wall, t0):
    """
    Converts raw AOA data to pandas DataFrame
    :param filename:       File name
    :return: alphas             pandas DataFrame with AOA values
    """
    # Read the entire file into a pandas DataFrame
    df = pd.read_csv(filename, sep='\s+', header=None, names=['Date', 'Time', 'Position', 'Turn'])
    # Filter out rows that do not have exactly 4 parts (though this should not happen with the current read_csv)
    df = df.dropna()

    # Compute the absolute sensor position in degrees
    abs_sensor_pos_deg = - df['Position'] / 2**14 * 360 - df['Turn'] * 360 + 214.73876953125
    # Compute the gear ratio and alpha
    gear_ratio = 60 / (306 * 2)
    df['alpha'] = abs_sensor_pos_deg * gear_ratio

    # Combine Date and Time into a single pandas datetime column
    df['Time'] = pd.to_datetime(df['Date'] + ' ' + df['Time'])
    # Select only the relevant columns
    df = df[['Time', 'alpha']]

    # Convert start time to milliseconds since it is easier to handle arithmetic operations
    start_time_ms = t0.timestamp() * 1000

    # Calculate the time difference in milliseconds from the first row
    time_diff_ms = df['Time'] - df['Time'].iloc[0]

    # Add this difference to the start time (in milliseconds) and convert back to datetime
    df['Time'] = pd.to_datetime(pd.Timestamp(start_time_ms, unit='ms') + time_diff_ms, unit='ms')

    # apply wind tunnel wall corrections
    df.loc[:, "alpha"] = df["alpha"] * (1 + sigma_wall)

    return df
def read_GPS(filename):
    df = pd.read_csv(filename, header=None)
    # Apply the parsing function to each row
    parsed_data = df.apply(parse_gprmc_row, axis=1)

    # Extract the columns from the parsed data
    df_parsed = pd.DataFrame(parsed_data.tolist(), columns=['Time', 'Latitude', 'Longitude', 'U_GPS'])

    # Drop rows with any None values (if any invalid GPRMC sentences)
    df_parsed = df_parsed.dropna()

    df_parsed["Time"] = df_parsed["Time"].dt.tz_localize('UTC')

    return df_parsed
def parse_gprmc_row(row):
    """
    processes GPS data in gprmc format
    :param row:
    :return:
    """
    parts = row.tolist()
    parts = [str(part) for part in parts]
    if len(parts) >= 13 and parts[0] == '$GPRMC' and parts[2] == 'A':
        try:
            time_str = parts[1]
            date_str = parts[9]
            latitude = float(parts[3][:2]) + float(parts[3][2:]) / 60.0
            if parts[4] == 'S':
                latitude = -latitude
            longitude = float(parts[5][:2]) + float(parts[5][2:]) / 60.0
            if parts[6] == 'W':
                longitude = -longitude
            gps_speed = float(parts[7]) * 1.852/3.6

            # Convert time and date to datetime
            datetime_str = date_str + time_str
            seconds, microseconds = datetime_str.split('.')
            microseconds = microseconds.ljust(6, '0')  # Pad to ensure 6 digits
            datetime_str = f"{seconds}.{microseconds}"
            datetime_format = '%d%m%y%H%M%S.%f'
            datetime_val = pd.to_datetime(datetime_str, format=datetime_format)

            return datetime_val, latitude, longitude, gps_speed
        except (ValueError, IndexError) as e:
            # Handle any parsing errors
            return None, None, None, None
    else:
        return None, None, None, None
def read_drive(filename, t0):
    """
    --> Reads drive data of wake rake (position and speed) into pandas DataFrame
    --> combines Date and Time to one pandas datetime column
    --> drive time = master time
    :param filename:       File name
    :return: df            pandas DataFrame with drive data
    """
    # list of column numbers in file to be read
    col_use = [0, 1, 2, 3]
    # how columns are named
    col_name = ['Date', 'Time', 'Rake Position', 'Rake Speed']

    # read file
    df = pd.read_csv(filename, sep="\s+", skiprows = 1, header=None, names=col_name, usecols=col_use,
                     on_bad_lines='skip', engine='python')

    # Combine Date and Time into a single pandas datetime column
    df['Time'] = pd.to_datetime(df['Date'] + ' ' + df['Time'])

    # drop date column (date column may generate problems when synchronizing data)
    df = df.drop(columns='Date')

    # Convert start time to milliseconds since it is easier to handle arithmetic operations
    start_time_ms = t0.timestamp() * 1000

    # Calculate the time difference in milliseconds from the first row
    time_diff_ms = df['Time'] - df['Time'].iloc[0]

    # Add this difference to the start time (in milliseconds) and convert back to datetime
    df['Time'] = pd.to_datetime(pd.Timestamp(start_time_ms, unit='ms') + time_diff_ms, unit='ms')

    return df
def read_DLR_pressure_scanner_file(filename, n_sens, t0):
    """
    Converts raw sensor data to pandas DataFrame
    :param filename:            File name
    :param n_sens:              number of sensors
    :param t0:                  time of first timestamp
    :return:                    pandas DataFrame with absolute time and pressures
    """

    # Convert start time to milliseconds since it is easier to handle arithmetic operations
    start_time_ms = t0.timestamp() * 1000

    # usual filename: "20230804-235818_static_K0X.dat"; drops .dat and splits name at underscores
    namelist = filename.rstrip(".dat").split("_")
    # generates base for column name of sensors; pattern: static_K0X_Y
    unit_name = "_".join(namelist[-2:])

    # generates final column name for DataFrame (time and static pressure sensor)
    columns = ["Time"] + [unit_name + f"_{i}" for i in range(1, n_sens+1)]

    # loading data into DataFrame. First line is skipped by default, because data is usually incorrect
    df = pd.read_csv(filename, sep="\s+", skiprows=1, header=None, on_bad_lines='skip')
    # if not as many columns a number of sensors (+2 for time and timedelta columns), then raise an error
    assert len(df.columns) == n_sens+2

    # drop timedelta column
    df = df.iloc[:, :-1]

    # drop outliers (all pressures greater than 115% of the median value and lower than 85 % of the median value)
    # Define the criteria for outliers
    lower_threshold = 0.85
    upper_threshold = 1.07

    # Iterate through each column to identify and filter out outliers
    for column in df.columns[1:]:
        median_value = df[column].median()
        lower_bound = lower_threshold * median_value
        upper_bound = upper_threshold * median_value
        df = df[(df[column] >= lower_bound) & (df[column] <= upper_bound)]

    # assign column names
    df.columns = columns

    # drop lines with missing data
    df = df.dropna().reset_index(drop=True)

    # remove outliers
    #df = df[(np.abs(stats.zscore(df)) < 3).all(axis=1)].reset_index(drop=True)

    # Calculate the time difference in milliseconds from the first row
    time_diff_ms = df['Time'] - df['Time'].iloc[0]

    # Add this difference to the start time (in milliseconds) and convert back to datetime
    df['Time'] = pd.to_datetime(start_time_ms + time_diff_ms, unit='ms')

    return df
def synchronize_data(merge_dfs_list):
    """
    synchronizes and interpolates sensor data, given in pandas DataFrames with a timestamp
    :param merge_dfs_list:      list of pandas DataFrames containing sensor data. Must contain "Time" column in
                                datetime format
    :return: merged_df          merged dataframe with all sensor data, interpolated according time
    """

    # Merge the DataFrames using merge_asof
    merged_df = merge_dfs_list[0]
    start = merged_df.loc[0, 'Time']
    end = merged_df.loc[len(merged_df.index)-1, 'Time']
    merged_df = merged_df.sort_values(by="Time", ignore_index=True)
    merged_df = merged_df.loc[(merged_df.Time >= start) & (merged_df.Time <= end)]
    for df in merge_dfs_list[1:]:
        start = df.loc[0, 'Time']
        end = df.loc[len(df.index)-1, 'Time']
        df = df.sort_values(by="Time", ignore_index=True)
        df = df.loc[(df.Time >= start) & (df.Time <= end)]
        merged_df = pd.merge_asof(merged_df, df.sort_values(by="Time", ignore_index=True), on='Time',
                                  tolerance=pd.Timedelta('1ms'), direction='nearest')

    # Set the index to 't_abs' to use time-based interpolation
    merged_df.set_index('Time', inplace=True)

    # Interpolate missing values using time-based interpolation
    merged_df = merged_df.interpolate(method='time')

    # localize index of df_sync to UTC
    merged_df.index = merged_df.index.tz_localize("UTC")

    return merged_df
def read_airfoil_geometry(filename, c, foil_source, eta_flap, pickle_file=""):
    """
    --> searchs for pickle file in WD, if not found it creates a new pickle file
    --> generates pandas DataFrame with assignment of sensor unit + port to measuring point from Excel and renames
    --> adds 's' positions of the measuring points from Excel (line coordinate around the profile,
        starting at trailing edge)
    --> reads 'Kommentar' column of excel a nd drops sensors with status 'inop'
    --> calculates x and y position of static pressure points with airfoil coordinate file
    --> calculates x_n and y_n normal vector, tangential to airfoil surface of static pressure points
        with airfoil coordinate file

    :param filename:            file name of Excel eg. "Messpunkte Demonstrator.xlsx".
    :param c:                   airfoil chord length
    :param foil_source:         string, path of airfoil coordinate file
    :param eta_flap:            flap deflection angle
    :param pickle_file:         path to pickle file with airfoil information
    :return df_airfoil:         DataFrame with info described above
    """

    # initialize airfoilTools object
    foil = at.Airfoil(foil_source)

    if os.path.exists(pickle_file):
        with open(pickle_file, 'rb') as file:
            df, eta_flap_read = pickle.load(file)

    if not os.path.exists(pickle_file) or eta_flap_read != eta_flap:

        if eta_flap != 0.0:
            foil.flap(xFlap=0.8, yFlap=0, etaFlap=eta_flap)

        # Read Excel file
        df = pd.read_excel(filename, usecols="A:F", skiprows=1, skipfooter=1)# Read the Excel file
        df = df.dropna(subset=['Sensor unit K', 'Sensor port'])
        df = df.drop(df[df["Kommentar"] == "inop"].index).reset_index(drop=True)
        df = df.astype({'Messpunkt': 'int32', 'Sensor unit K': 'int32', 'Sensor port': 'int32'})

        # append virtual trailing edge pressure taps (pressure is mean between last sensor at to and bottom side)
        df_virt_top = pd.DataFrame([[np.nan, "virtual_top", 0, -1, -1, np.nan]], columns=df.columns)
        # virtual bottom trailing edge tap: s value must be calculated
        df_virt_bot = pd.DataFrame([[np.nan, "virtual_bot", at.s_curve(foil.u[-1], foil.tck)*c*1000, -1, -1, np.nan]],
                                   columns=df.columns)
        df = pd.concat([df_virt_top, df, df_virt_bot]).reset_index(drop=True)

        df["s"] = df["Position [mm]"]/(c*1000)
        df["x"] = np.nan
        df["y"] = np.nan

        df["x_n"] = np.nan
        df["y_n"] = np.nan

        u_taps = np.zeros(len(df.index))

        for i, s in enumerate(df["s"]):
            res = optimize.root_scalar(at.s_curve, args=(foil.tck, s), x0=0, fprime=at.ds)
            u_taps[i] = res.root
            coords_tap = interpolate.splev(u_taps[i], foil.tck)
            df.loc[i, "x"] = coords_tap[0]
            df.loc[i, "y"] = coords_tap[1]
            n_tap = np.dot(at.tangent(u_taps[i], foil.tck)[0], np.array([[0, -1], [1, 0]]))
            df.loc[i, "x_n"] = n_tap[0]
            df.loc[i, "y_n"] = n_tap[1]

        if pickle_file != "":
            with open(pickle_file, 'wb') as file:
                pickle.dump([df, eta_flap], file)

    return df, foil
def calc_airspeed_wind(df, prandtl_data, T, l_ref):
    """
    --> calculates wind component in free stream direction

    :param df:          pandas DataFrame containing 'U_CAS' and 'U_GPS' column
    :return: df         pandas DataFrame with wind component column
    """


    colname_total = prandtl_data['unit name total'] + '_' + str(prandtl_data['i_sens_total'])
    colname_static = prandtl_data['unit name static'] + '_' + str(prandtl_data['i_sens_static'])
    ptot = df[colname_total]
    pstat = df[colname_static]

    # density of air according to International Standard Atmosphere (ISA)
    rho_ISA = 1.225
    R_s = 287.0500676
    # calculate derived variables (dynamic viscosity). Has to be calculated online if we chose to add a temp sensor
    # Formula from https://calculator.academy/viscosity-of-air-calculator/
    mu = (1.458E-6 * T ** (3 / 2)) / (T + 110.4)

    df['U_CAS'] = np.sqrt(2 * (ptot - pstat) / rho_ISA)

    # calculate air speeds
    rho = pstat / (R_s * T)
    df['U_TAS'] = np.sqrt(np.abs(2 * (ptot - pstat) / rho))
    df['Re'] = df['U_TAS'] * l_ref * rho / mu

    # calculating wind component in free stream direction
    #df['wind_component'] = df['U_TAS'] - df['U_GPS']

    return df
def calc_cp(df, prandtl_data, pressure_data_ident_strings):
    """
    calculates pressure coefficient for each static port on airfoil

    :param df:                          pandas DataFrame with synchronized and interpolated measurement data
    :param prandtl_data:                dict with "unit name static", "i_sens_static", "unit name total" and
                                        "i_sens_total".
                                        This specifies the sensor units and the index of the sensors of the Prandtl
                                        probe total
                                        pressure sensor and the static pressure sensor
    :param pressure_data_ident_strings: list of strings, which are contained in column names, which identify
                                        pressure sensor data
    :return: df                         pandas DataFrame with pressure coefficient in "static_K0X_Y" columns for
                                        every
                                        measuring point
    """
    # picks names of prandtl sensors
    colname_total = prandtl_data['unit name total'] + '_' + str(prandtl_data['i_sens_total'])
    colname_static = prandtl_data['unit name static'] + '_' + str(prandtl_data['i_sens_static'])
    # picks columns with prandtl data
    ptot = df[colname_total]
    pstat = df[colname_static]

    # column names of all pressure sensor data
    pressure_cols = []
    for string in pressure_data_ident_strings:
        pressure_cols += [col for col in df.columns if string in col]

    # apply definition of c_p
    df[pressure_cols] = df[pressure_cols].apply(lambda p_col: (p_col - pstat)/(ptot - pstat))

    df.replace([np.inf, -np.inf], 0., inplace=True)

    return df
def calc_cl_cm_cdp(df, df_airfoil, at_airfoil, flap_pivots=[], lambda_wall=0., sigma_wall=0., xi_wall=0.):
    """

    :param df:
    :param df_airfoil:
    :param at_airfoil:
    :param flap_pivots:     position of flap hinge; TE: one point, if TE and LE: two points
    :param lambda_wall:
    :param sigma_wall:
    :param xi_wall:
    :return:
    """

    # calculate tap normal vector components on airfoil surface projected to aerodynamic coordinate system
    n_proj_z = np.dot(df_airfoil[['x_n', 'y_n']].to_numpy(), np.array([-np.sin(np.deg2rad(df['alpha'])),
                                             np.cos(np.deg2rad(df['alpha']))])).T
    n_proj_x = np.dot(df_airfoil[['x_n', 'y_n']].to_numpy(), np.array([np.cos(np.deg2rad(df['alpha'])),
                                             np.sin(np.deg2rad(df['alpha']))])).T

    # assign tap index to sensor unit and sensor port
    sens_ident_cols = ["static_K0{0:d}_{1:d}".format(df_airfoil.loc[i, "Sensor unit K"],
                                                     df_airfoil.loc[i, "Sensor port"]) for i in df_airfoil.index[1:-1]]
    # calculate virtual pressure coefficient
    df["static_virtual_top"] = df["static_virtual_bot"] = (df[sens_ident_cols[0]] + df[sens_ident_cols[-1]])/2
    # re-arrange columns
    cols = df.columns.to_list()
    cols = cols[:3*32] + cols[-2:] + cols[3*32:-2]
    df = df[cols].copy()
    sens_ident_cols = ["static_virtual_top"] + sens_ident_cols + ["static_virtual_bot"]


    # calculate cl
    cp = df[sens_ident_cols].to_numpy()
    df.loc[:, "cl"] = -integrate.simpson(cp * n_proj_z, x=df_airfoil['s'])

    # calculate pressure drag
    df.loc[:, "cdp"] = -integrate.simpson(cp * n_proj_x, x=df_airfoil['s'])

    n_taps = df_airfoil[['x_n', 'y_n']].to_numpy()
    s_taps = df_airfoil['s']

    # calculate hinge moment
    r_ref = np.tile(np.array([0.25, 0]), [len(df_airfoil.index), 1]) - df_airfoil[['x', 'y']].to_numpy()
    df.loc[:, "cm"] = -integrate.simpson(cp * np.tile(np.cross(n_taps, r_ref), [len(df.index), 1]),
                                  x=s_taps)

    # calculate hinge moment of trailing edge flap
    n_flaps = len(flap_pivots)
    flap_pivots = np.array(flap_pivots)
    TE_flap = False
    LE_flap = False
    if n_flaps >= 1:
        TE_flap = True
        flap_pivot_TE = flap_pivots
    if n_flaps > 1:
        LE_flap = True
        flap_pivot_LE = flap_pivots[0, :]
        flap_pivot_TE = flap_pivots[1, :]
    if TE_flap:
        r_ref_F = df_airfoil[['x', 'y']].to_numpy() - np.tile(flap_pivot_TE, [len(df_airfoil.index), 1])
        mask = df_airfoil['x'].to_numpy() >= flap_pivot_TE[0]
        df.loc[:, "cmr_TE"] = integrate.simpson(cp[:, mask] * np.tile(np.cross(n_taps[mask], r_ref_F[mask, :]),
                                              [len(df.index), 1]), x=s_taps[mask])
    if LE_flap:
        r_ref_F = df_airfoil[['x', 'y']].to_numpy() - np.tile(flap_pivot_LE, [len(df_airfoil.index), 1])
        mask = df_airfoil['x'].to_numpy() <= flap_pivot_LE[0]
        df.loc[:, "cmr_LE"] = integrate.simpson(cp[:, mask] * np.tile(np.cross(n_taps[mask], r_ref_F[mask, :]),
                                              [len(df.index), 1]), x=s_taps[mask])
        # apply wind tunnel wall corrections
        df.loc[:, "cl"] = df["cl"] * (1 - 2 * lambda_wall * (sigma_wall + xi_wall) - sigma_wall)

        df.loc[:, "cm"] = df["cm"] * (1 - 2 * lambda_wall * (sigma_wall + xi_wall))

    # finally apply wall correction to cp's (after calculation of lift and moment coefficients.
    # Otherwise, correction would be applied twice
    df.loc[:, sens_ident_cols] = (1 - 2 * lambda_wall * (sigma_wall + xi_wall) - sigma_wall) * df[sens_ident_cols]

    return df, sens_ident_cols, cp
def calc_cd(df, l_ref, lambda_wall, sigma_wall, xi_wall):
    """

    :param df:
    :return:
    """

    h_stat = 100
    h_tot = 93

    z_stat = np.linspace(-h_stat / 2, h_stat / 2, 5, endpoint=True)
    z_tot = np.linspace(-h_tot / 2, h_tot / 2, 32, endpoint=True)
    # it is assumed, that 0th sensor is defective (omit that value)
    z_tot = z_tot[1:]

    cp_stat_raw = df.filter(regex='^pstat_rake_').to_numpy()
    cp_stat_int = interpolate.interp1d(z_stat, cp_stat_raw, kind="linear", axis=1)

    cp_stat = cp_stat_int(z_tot)

    cp_tot = df.filter(regex='^ptot_rake_').to_numpy()
    # it is assumed, that 0th sensor is defective (omit that value)
    cp_tot = cp_tot[:, 1:]

    # Measurement of Proﬁle Drag by the Pitot-Traverse Method
    d_cd_jones = 2 * np.sqrt(np.abs((cp_tot - cp_stat))) * (1 - np.sqrt(np.abs(cp_tot)))

    # integrate integrand with simpson rule
    cd = integrate.simpson(d_cd_jones, z_tot) * 1 / (l_ref*1000)

    # apply wind tunnel wall corrections
    cd = cd * (1 - 2 * lambda_wall * (sigma_wall + xi_wall))

    df["cd"] = cd

    return df
def apply_calibration_offset(filename, df):

    with open(filename, "rb") as file:
        calibr_data = pickle.load(file)

    l_ref = calibr_data[6]

    # flatten calibration data list, order like df pressure sensors
    pressure_calibr_data = calibr_data[2] + calibr_data[3] + calibr_data[4] + calibr_data[1] + calibr_data[0]
    # append zero calibration offsets for alpha, Lat/Lon, U_GPS and Rake Position
    pressure_calibr_data += [0]*(len(df.columns) - len(pressure_calibr_data))

    df_calibr_pressures = pd.DataFrame(data=[pressure_calibr_data], columns=df.columns)
    # repeat calibration data
    df_calibr_pressures = df_calibr_pressures.loc[df_calibr_pressures.index.repeat(len(df.index))]
    df_calibr_pressures.index = df.index

    # Apply calibration offsets
    df = df - df_calibr_pressures

    return df, l_ref
def apply_calibration_20sec(df, calibration_output_filename="manual_calibration_data.p"):
    """
    uses first 20 seconds to calculate pressure sensor calibration offsets
    :param df:
    :return:
    """

    # select only pressures
    df_pres = df.iloc[:, :len(df.columns)-6]

    # Select the first 20 seconds of data
    first_20_seconds = df_pres[df_pres.index < df_pres.index[0] + pd.Timedelta(seconds=20)]

    # Calculate the mean for each sensor over the first 20 seconds
    mean_values = first_20_seconds.mean(axis=0)

    # Use these means to calculate the offsets for calibration
    offsets = mean_values - mean_values.mean()

    # Apply the calibration to the entire DataFrame
    df.iloc[:, :len(df.columns)-6] = df.iloc[:, :len(df.columns)-6] - offsets

    with open(calibration_output_filename, "wb") as file:
        pickle.dump(offsets, file)

    return df
def apply_manual_calibration(df, calibration_filename="manual_calibration_data.p"):
    """
    uses first 20 seconds to calculate pressure sensor calibration offsets
    :param df:
    :return:
    """

    with open(calibration_filename, "rb") as file:
        offsets = pickle.load(file)

    # Apply the calibration to the entire DataFrame
    df.iloc[:, :len(df.columns) - 6] = df.iloc[:, :len(df.columns) - 6] - offsets

    return df
def calc_wall_correction_coefficients(df_airfoil, filepath, l_ref):
    """
    calculate wall correction coefficients according to
    Abbott and van Doenhoff 1945: Theory of Wing Sections
    and
    Althaus 2003: Tunnel-Wall Corrections at the Laminar Wind Tunnel
    :param df_airfoil:      pandas dataframe with x and y position of airfoil contour
    :param df_cp:           pandas dataframe with cp values
    :return:                wall correction coefficients
    """

    #read symmetrical airfoil data
    column_names = ['x', 'y', 'cp']
    df_wall_correction_cp = pd.read_csv(filepath, names=column_names, skiprows=3, delim_whitespace=True)

    # model - wall distances:
    d1 = 0.7
    d2 = 1.582

    # cut off bottom airfoil side
    df_wall_correction_cp = df_wall_correction_cp.iloc[:np.argmin(df_wall_correction_cp["x"]) + 1, :]
    # and flip it
    df_wall_correction_cp = df_wall_correction_cp.iloc[::-1, :].reset_index(drop=True)

    # calculate surface contour gradient dy_t/dx as finite difference scheme. first and last value are calculated
    # with forward and backward difference scheme, respectively and all other values with central difference
    # scheme
    dyt_dx = np.gradient(df_wall_correction_cp["y"])/np.gradient(df_wall_correction_cp["x"])

    # calculate v/V_inf
    v_V_inf = np.sqrt(1 - df_wall_correction_cp["cp"].values)

    # calculate lambda (warning: Lambda of Althaus is erroneus, first y factor forgotten)
    lambda_wall_corr = integrate.simpson(y=16 / np.pi * df_wall_correction_cp["y"].values * v_V_inf *
                                                np.sqrt(1 + dyt_dx ** 2), x=df_wall_correction_cp["x"].values)

    # calculate sigma
    sigma_wall_corr = np.pi ** 2 / 48 * l_ref**2 * 1 / 2 * (1 / (2 * d1) + 1 / (2 * d2)) ** 2

    # correction for model influence on static reference pressure
    # TODO: Re-calculate this using a panel method or with potential flow theory
    xi_wall_corr = -0.00335 * l_ref**2

    return lambda_wall_corr, sigma_wall_corr, xi_wall_corr
def plot_specify_segment(df_cp, df_p_abs, df_segments, U_cutoff=10, plot_pstat=False, unit_sens_pstat="static_K04_31", i_seg_plot=None):
    """

    :param df_sync:
    :return:
    """

    # plot U_CAS over time
    fig, ax, = plt.subplots()
    ax.plot(df_cp["U_CAS"])
    ax.set_xlabel("$Time$")
    ax.set_ylabel("$U_{CAS} [m/s]$")
    ax.set_title("$U_{CAS}$ vs. Time")
    ax.xaxis.set_major_formatter(DateFormatter("%M:%S"))
    ax.grid()
    for index, row in df_segments.iterrows():
        if index == i_seg_plot:
            color = "green"
        else:
            color = 'lightgray'
        ax.axvspan(row['start'], row['end'], color=color, alpha=0.5)

    # plot alpha, cl, cm, cmr over time
    fig, host = plt.subplots()
    # Create twin axes on the right side of the host axis
    ax1 = host.twinx()
    ax2 = host.twinx()
    ax3 = host.twinx()
    if plot_pstat:
        ax4 = host.twinx()
    # Offset the right twin axes so they don't overlap
    ax1.spines['right'].set_position(('outward', 120))
    ax2.spines['right'].set_position(('outward', 60))
    ax3.spines['right'].set_position(('outward', 0))
    if plot_pstat:
        ax4.spines['right'].set_position(('outward', 180))

    # filter data
    window = 201
    polyorder = 2
    cl_filt = savgol_filter(df_cp.loc[df_cp["U_CAS"] > U_cutoff, "cl"], window, polyorder)
    Re_filt = savgol_filter(df_cp["Re"], window, polyorder)
    cd_filt = savgol_filter(df_cp.loc[df_cp["U_CAS"] > U_cutoff, "cd"], window, polyorder)
    p_stat_filt = savgol_filter(df_p_abs[unit_sens_pstat], window, polyorder)

    # Set plot lines
    ax1.plot(df_cp.loc[df_cp["U_CAS"] > U_cutoff].index, df_cp.loc[df_cp["U_CAS"] > U_cutoff, "alpha"], "k-", label=r"$\alpha$", zorder=5)
    line = ax2.plot(df_cp.index, df_cp["Re"], "y-", label=r"$Re$", zorder=4, alpha=0.35)
    ax2.plot(df_cp.index, Re_filt, color=line[0].get_color())
    line = host.plot(df_cp.loc[df_cp["U_CAS"] > U_cutoff].index, df_cp.loc[df_cp["U_CAS"] > U_cutoff, "cl"], label="$c_l$", zorder=3, alpha=0.35)
    host.plot(df_cp.loc[df_cp["U_CAS"] > U_cutoff].index, cl_filt, color=line[0].get_color())
    #host.plot(df.loc[df["U_CAS"] > U_cutoff].index, df.loc[df["U_CAS"] > U_cutoff, "cm"], label="$c_{m}$", zorder=2, alpha=0.35)
    line = ax3.plot(df_cp.loc[df_cp["U_CAS"] > U_cutoff].index, df_cp.loc[df_cp["U_CAS"] > U_cutoff, "cd"], color="red", label="$c_d$", zorder=1, alpha=0.35)
    ax3.plot(df_cp.loc[df_cp["U_CAS"] > U_cutoff].index, cd_filt, color=line[0].get_color())
    ax3.set_ylim([0., cd_filt.max()])
    if plot_pstat:
        line = ax4.plot(df_p_abs.index, df_p_abs[unit_sens_pstat], color="green", label="$p_{stat}$", zorder=1, alpha=0.35)
        ax4.plot(df_p_abs.index, p_stat_filt, color=line[0].get_color())
    for index, row in df_segments.iterrows():
        if index == i_seg_plot:
            color = "green"
        else:
            color = 'lightgray'
        host.axvspan(row['start'], row['end'], color=color, alpha=0.5)

    # Formatting the x-axis to show minutes and seconds
    host.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))
    #host.xaxis.set_major_formatter(ticker.FormatStrFormatter('%0.1f'))
    # Setting labels
    host.set_xlabel("$Time[mm:ss]$")
    ax1.set_ylabel(r"$\alpha~\mathrm{[^\circ]}$")
    host.set_ylabel("$c_l$")
    ax2.set_ylabel("$Re$")
    ax3.set_ylabel("$c_d$")
    if plot_pstat:
        ax4.set_ylabel("$p_{stat}~\mathrm{[Pa]}$")
    # Enabling grid on host
    host.grid()
    # Adding legends from all axes
    lines, labels = [], []
    axes = [host, ax1, ax2, ax3]
    if plot_pstat:
        axes.append(ax4)
    for ax in axes:
        line, label = ax.get_legend_handles_labels()
        lines.extend(line)
        labels.extend(label)
    host.tick_params(axis='x', labelrotation=80)
    fig.legend(lines, labels, loc='upper right')


    """
    # plot path of car
    fig5, ax3 = plt.subplots()
    ax3.plot(df["Longitude"], df["Latitude"], "k-")
    ax3.plot(df.loc[df["U_CAS"] > 5, "Longitude"], df.loc[df["U_CAS"] > 5, "Latitude"], "g-")
    """

    # plot c_d, rake position and rake speed over time
    #fig6, ax6 = plt.subplots()
    #ax7 = ax6.twinx()

    """ax6.set_xlabel("$Time$")
    ax7.set_xlabel("Rake Position / Speed")
    ax6.set_ylabel("$c_d$")
    ax7.set_ylabel("$Rake Position [mm]$")
    ax6.set_title("$c_d$ vs. Time")
    ax6.xaxis.set_major_formatter(DateFormatter("%M:%S"))
    fig6.legend()
    ax6.grid()"""

    return
def plot_cp_x_and_wake(df, df_airfoil, at_airfoil, sens_ident_cols, df_segments, i_seg):
    """
    plots cp(x) and wake depression (x) at certain operating points (alpha, Re and beta)
    :param df:      pandas dataframe with index time and data to be plotted
    :param t:       index number of operating point (=time)
    :return:
    """
    h_stat = 100
    h_tot = 93

    t_start = np.abs(df_sync.index-df_segments.loc[i_seg, "start"]).argmin()
    t_end = np.abs(df_sync.index-df_segments.loc[i_seg, "end"]).argmin()

    # plot cp(x)
    fig, ax = plt.subplots()
    ax_cp = ax.twinx()
    ax.plot(at_airfoil.coords[:, 0], at_airfoil.coords[:, 1], "k-")
    ax.plot(df_airfoil["x"], df_airfoil["y"], "k.")
    ax_cp.plot(df_airfoil["x"], df[sens_ident_cols].iloc[t_start:t_end].mean(), "r.-")
    # Calculate mean values and standard deviations over the specified time interval
    mean_cp_values = df[sens_ident_cols].iloc[t_start:t_end].mean()
    std_cp_values = df[sens_ident_cols].iloc[t_start:t_end].std()
    # Plot the mean cp values with error bars
    ax_cp.errorbar(df_airfoil["x"], mean_cp_values, yerr=std_cp_values, fmt='r.-', ecolor='gray', elinewidth=1,capsize=2)
    ylim_u, ylim_l = ax_cp.get_ylim()
    ax_cp.set_ylim([ylim_l, ylim_u])
    ax.set_xlabel("$x$")
    ax.set_ylabel("$y$")
    ax_cp.set_ylabel("$c_p$")
    ax.set_title("Pressure distribution over airfoil")
    ax_cp.grid()
    ax.axis("equal")

    # plot wake depression(x)
    # extract total pressure of wake rake from dataframe
    cols = df.columns.to_list()
    cols = df.filter(regex='^ptot')
    # it is assumed, that 0th sensor is defective (omit that value)
    cols = cols.iloc[:, 1:]

    # positions of total pressure sensors of wake rake
    z_tot = np.linspace(-h_tot / 2, h_tot / 2, 32, endpoint=True);
    # it is assumed, that 0th sensor is defective (omit that value)
    z_tot = z_tot[1:]



    fig, ax = plt.subplots()
    ax_cp = ax.twiny()
    ax.plot(at_airfoil.coords[:, 0]*100, at_airfoil.coords[:, 1]*100, "k-")
    ax_cp.plot(cols.iloc[t_start:t_end].mean(), z_tot, "r.-")
    # Calculate mean and standard deviation over the specified time interval
    mean_ptot_values = cols.iloc[t_start:t_end].mean()
    std_ptot_values = cols.iloc[t_start:t_end].std()
    # Plot the mean ptot values with error bars
    ax_cp.errorbar(mean_ptot_values, z_tot, xerr=std_ptot_values, fmt='r.-', ecolor='gray', elinewidth=1, capsize=2)
    ylim_l, ylim_u = ax_cp.get_ylim()
    ax_cp.set_ylim([ylim_l, ylim_u])
    ax.set_xlabel("$x$")
    ax.set_ylabel("$z$")
    ax_cp.set_xlabel("$c_p$")
    ax.set_title("Wake Depression")
    ax_cp.grid()
    ax.axis("equal")

    return
def plot_3D(df):
    """

    :param df:
    :return:
    """


    # Create a new figure for the 3D plot
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')

    # Extract data for the plot
    x = df.index
    y = df['U_CAS']
    z = df['Rake Position']

    # Convert the datetime index to a numerical format for plotting
    x_num = x.map(pd.Timestamp.toordinal)

    # Create the 3D scatter plot
    ax.scatter(x_num, y, z, c='r', marker='o')

    # Set labels
    ax.set_xlabel('Time')
    ax.set_ylabel('c_d')
    ax.set_zlabel('Rake Position')

    # Convert the numerical x-axis back to dates for better readability
    ax.set_xticklabels(x.strftime("%H:%M"))

    # Show the plot


    return
def calc_mean(df, alpha, Re):

    """calculates mean values of AOA, lift-, drag- and moment coefficients for a given alpha (automativally given from
    calc_means when called) and an entered Reynoldsnumber
    :param df:          pandas dataframe with column names: alpha, cl, cd, cm (given from plot polars function)
    :param alpha:       automativally given from calc_means when called
    :param Re:          desired Reynoldsnumber for the test (needs to be typed in function call)
    :return:"""
    
    # define Intervalls (might be adapted)
    delta_alpha = 0.09
    min_alpha = alpha - delta_alpha
    max_alpha = alpha + delta_alpha
    delta_Re = 0.2e6
    min_Re = Re - delta_Re
    max_Re = Re + delta_Re

    # conditions for representative values
    condition = ((df["alpha"] > min_alpha) &
                 (df["alpha"] < max_alpha) &
                 (df["Re"] > min_Re) &
                 (df["Re"] < max_Re))# &
                 #(df["Rake Speed"] != 0))



    # pick values which fulfill the condition
    col_alpha = df.loc[condition, "alpha"]
    col_cl = df.loc[condition, "cl"]
    col_cd = df.loc[condition, "cd"]
    col_cm = df.loc[condition, "cm"]

    # calculate mean values
    mean_alpha = col_alpha.mean()
    mean_cl = col_cl.mean()
    mean_cd = col_cd.mean()
    mean_cm = col_cm.mean()

    return mean_alpha, mean_cl, mean_cd, mean_cm
def prepare_polar_df(df_sync, df_segments):
    """
    iterates over alpha [1,17] deg and calculates to each alpha the mean values of cl, cd and cm; if alpha and Re
    criteria are not fulfilled, moves on to next alpha value
    :param df_sync:                  pandas dataframe with all data to be plotted
    :param alpha_range:         AOA interval for polar
    :param Re:                  desired Reynoldsnumber for polar
    :return: df_polars            df with polar values ready to be plotted
    """
    # create a new dataframe with specified column names
    cols = ["alpha", "Re", "U_CAS", "U_TAS", "cl","cm", "cd", "cdp", "cmr_LE", "cmr_TE"]
    data = []
    for i in range(len(df_segments.index)):
        start_time = df_segments.loc[i, "start"]
        end_time = df_segments.loc[i, "end"]
        df_seg = df_sync.loc[(df_sync.index >= start_time) & (df_sync.index <= end_time), :]
        data_row = []
        for col in cols:
            data_row.append(df_seg[col].mean())
            data_row.append(df_seg[col].std())
        data.append(data_row)

    cols = np.array([[col, col+"_std"] for col in cols]).flatten()
    df_polar = pd.DataFrame(data, columns=cols)

    return df_polar
def plot_polars(df):
    """

    :param df_polars:
    :return:
    """
    # plot cl(alpha)
    fig, ax = plt.subplots()
    ax.plot(df["alpha"], df["cl"], "k.", linestyle='-')
    ax.set_xlabel(r"$\alpha$")
    ax.set_ylabel("$c_l$")
    ax.set_title("$c_l$ vs. alpha")
    ax.grid()

    # plot cl(cd)
    fig2, ax2 = plt.subplots()
    ax2.plot(df["cd"], df["cl"], "k.", linestyle='-')
    ax2.set_xlabel("$c_d$")
    ax2.set_ylabel("$c_l$")
    ax2.set_title("$c_l$ vs. $c_d$")
    ax2.grid()

    # plot cl(cm)
    fig3, ax3 = plt.subplots()
    ax3.plot(df["cm"], df["cl"], "k.", linestyle='-')
    ax3.set_xlabel("$c_m$")
    ax3.set_ylabel("$c_l$")
    ax3.set_title("$c_l$ vs. $c_m$")
    ax3.grid()

    # plot cm(alpha)
    fig4, ax4 = plt.subplots()
    ax4.plot(df["alpha"], df["cm"], "k.", linestyle='-')
    ax4.set_xlabel(r"$\alpha$")
    ax4.set_ylabel("$c_m$")
    ax4.set_title("$c_m$ vs. alpha")
    ax4.grid()



    return
def settling_time_average(df):
    '''
    visualizes the running average over time in order to analyze if the sweep time is sufficient or not
    :param df:      df_sync
    :return:
    '''

    Re=1e6
    alpha=-4
    # define Intervalls (might be adapted)
    delta_alpha = 0.2
    min_alpha = alpha - delta_alpha
    max_alpha = alpha + delta_alpha
    delta_Re = 0.1e6
    min_Re = Re - delta_Re
    max_Re = Re + delta_Re

    # conditions to achieve representative values
    condition = ((df["alpha"] > min_alpha) &
                 (df["alpha"] < max_alpha) &
                 (df["Re"] > min_Re) &
                 (df["Re"] < max_Re))# &
                 #(df["Rake Speed"] != 0))

    # pick values which fulfill the condition
    col_alpha = df.loc[condition, "alpha"]
    col_cl = df.loc[condition, "cl"]
    col_cd = df.loc[condition, "cd"]
    col_cm = df.loc[condition, "cm"]

    # visualize settling time of average calculation
    # Create a running average column
    col_cd['running_avg'] = col_cd.expanding().mean()

    # Plotting the running average
    plt.figure(figsize=(10, 6))
    plt.plot(col_cd['running_avg'].index, col_cd['running_avg'], label='Running Average', color='blue')
    plt.xlabel('Time')
    plt.ylabel('Running Average of cd')
    plt.title('Running Average of cd Over Time')
    plt.legend()
    plt.grid(True)


    return


if __name__ == '__main__':

    T_air = 288
    # Lower cutoff speed for plots
    U_cutoff = 10
    # specify test segment, which should be plotted
    i_seg_plot = 5

    airfoil = "Mü13-33"
    #airfoil = "B200"
    # constants and input data
    if airfoil == "Mü13-33":
        l_ref = 0.7
        # Raw data file prefix
        seg_def_files = ["T025_untrimmed.xlsx"]
        digitized_LWK_polar_files_clcd = ["Re8e5_beta15_cl-cd.txt"]
        digitized_LWK_polar_files_clalpha = ["Re8e5_beta15_cl-alpha.txt"]
        # set calibration type in seg_def Excel file ("20sec", "manual", "file")
        # set flap deflection in seg_def Excel file
        if os.getlogin() == 'joeac':
            WDIR = "C:/OneDrive/OneDrive - Achleitner Aerospace GmbH/ALF - General/Auto-Windkanal/07_Results/Mü13-33/2024-06-18"
            segments_def_dir = "C:/OneDrive/OneDrive - Achleitner Aerospace GmbH/ALF - General/Auto-Windkanal/07_Results/Mü13-33/testsegments_specification"
            digitized_LWK_polar_dir = "C:/OneDrive/OneDrive - Achleitner Aerospace GmbH/ALF - General/Auto-Windkanal/07_Results/Mü13-33/Digitized data Döller LWK/"
            ref_dat_path = "C:/OneDrive/OneDrive - Achleitner Aerospace GmbH/ALF - General/Auto-Windkanal/07_Results/Mü13-33/01_Reference Data/"
        else:
            WDIR = "D:/Python_Codes/Workingdirectory_Auswertung"
            segments_def_dir = "D:/Python_Codes/Rohdateien/Zeitabschnitte_Polaren"
            digitized_LWK_polar_dir = "D:/Python_Codes/Rohdateien/digitized_polars_doeller"
            ref_dat_path = "D:/Python_Codes/Workingdirectory_Auswertung/"
        prandtl_data = {"unit name static": "static_K04", "i_sens_static": 31,
                        "unit name total": "ptot_rake", "i_sens_total": 3}
                        #"unit name total": "static_K04", "i_sens_total": 32}

        foil_coord_path = os.path.join(ref_dat_path, "mue13-33-le15.dat")
        file_path_msr_pts = os.path.join(ref_dat_path, 'Messpunkte Demonstrator_Mue13-33.xlsx')
        pickle_path_msr_pts = os.path.join(ref_dat_path, 'Messpunkte Demonstrator.p')
        cp_path_wall_correction = os.path.join(ref_dat_path, 'mue13-33-le15-tgap0_14.cp')
    elif airfoil == "B200":
        l_ref = 0.5
        seg_def_files = ["T006_R011.xlsx"]
        digitized_LWK_polar_files_clcd = []
        digitized_LWK_polar_files_clalpha = []
        WDIR = "C:/OneDrive/OneDrive - Achleitner Aerospace GmbH/ALF - General/Auto-Windkanal/07_Results/B200/2023_09_26/T6_R011"
        segments_def_dir = "C:/OneDrive/OneDrive - Achleitner Aerospace GmbH/ALF - General/Auto-Windkanal/07_Results/B200/Testsegments_specification"
        digitized_LWK_polar_dir = ""
        ref_dat_path = "C:/OneDrive/OneDrive - Achleitner Aerospace GmbH/ALF - General/Auto-Windkanal/07_Results/B200/01_Reference Data/"
        prandtl_data = {"unit name static": "static_K04", "i_sens_static": 31,
                        "unit name total": "ptot_rake", "i_sens_total": 3}
        # "unit name total": "static_K04", "i_sens_total": 32}

        foil_coord_path = os.path.join(ref_dat_path, "B200-0_reinitialized.dat")
        file_path_msr_pts = os.path.join('C:/OneDrive/OneDrive - Achleitner Aerospace GmbH/ALF - General/Auto-Windkanal/03_Static pressure measurement system/Messpunkte Demonstrator/Messpunkte Demonstrator.xlsx')
        pickle_path_msr_pts = os.path.join(ref_dat_path, 'Messpunkte Demonstrator.p')
        cp_path_wall_correction = os.path.join(ref_dat_path, 'B200-0_reinitialized.cp')

    #******************************************************************************************************************
    #******************************************************************************************************************



    calibration_filename = '20240613-2336_manual_calibration_data.p'


    #******************************************************************************************************************
    #******************************************************************************************************************

    os.chdir(WDIR)

    list_of_df_polars = ([])
    list_of_polars = []
    list_of_eta_flaps = []

    for seg_def_file in seg_def_files:

        digitized_LWK_polar_paths = []
        for i in range(len(digitized_LWK_polar_files_clcd)):
            digitized_LWK_polar_paths.append([os.path.join(digitized_LWK_polar_dir, digitized_LWK_polar_files_clcd[i]),
                                              os.path.join(digitized_LWK_polar_dir, digitized_LWK_polar_files_clalpha[i])])


        flap_pivots = np.array([[0.2, 0.0], [0.8, 0.0]]) # LEF and TEF

        # get segments filenames
        segments_def_path = os.path.join(segments_def_dir, seg_def_file)


        # read raw data filenames
        raw_data_filenames = pd.read_excel(segments_def_path, skiprows=0, usecols="J").dropna().values.astype(
            "str").flatten()
        calibration_types = pd.read_excel(segments_def_path, skiprows=0, usecols="K").dropna().values.astype(
            "str").flatten()
        eta_flap = pd.read_excel(segments_def_path, skiprows=0, usecols="L").dropna().values.astype(
            "float").flatten()
        eta_flap = eta_flap[0]
        list_of_eta_flaps.append(eta_flap)
        # read segment times
        df_segments = pd.read_excel(segments_def_path, skiprows=1, usecols="A:H").ffill(axis=0)
        df_segments[["hh", "mm", "ss", "hh.1", "mm.1", "ss.1"]] = df_segments[["hh", "mm", "ss", "hh.1", "mm.1", "ss.1"]].astype(int)
        local_timezone = tzlocal.get_localzone_name()
        df_segments['start'] = pd.to_datetime(df_segments['dd'].astype(str) + ' ' +
                                              df_segments['hh'].astype(str) + ':' +
                                              df_segments['mm'].astype(str) + ':' +
                                              df_segments['ss'].astype(str),
                                              errors='coerce', utc=False).dt.tz_localize(local_timezone, ambiguous='NaT', nonexistent='shift_forward')
        df_segments['end'] = pd.to_datetime(df_segments['dd.1'].astype(str) + ' ' +
                                            df_segments['hh.1'].astype(str) + ':' +
                                            df_segments['mm.1'].astype(str) + ':' +
                                            df_segments['ss.1'].astype(str),
                                            errors='coerce', utc=False).dt.tz_localize(local_timezone, ambiguous='NaT', nonexistent='shift_forward')

        df_segments = df_segments[['start', 'end']]

        # read airfoil data
        df_airfoil, airfoil = read_airfoil_geometry(file_path_msr_pts, c=l_ref, foil_source=foil_coord_path, eta_flap=eta_flap,
                                                    pickle_file=pickle_path_msr_pts)
        # calculate wall correction coefficients
        lambda_wall, sigma_wall, xi_wall = calc_wall_correction_coefficients(df_airfoil, cp_path_wall_correction, l_ref)

        df_sync=pd.DataFrame()
        list_of_dfs = []

        for i, filename in enumerate(raw_data_filenames):
            calibration_type = calibration_types[i]
            if calibration_type not in ["file"," 20sec"]:
                calibration_filename = calibration_type
                calibration_type= "manual"

            file_path_drive = os.path.join(WDIR, f"{filename}_drive.dat")
            file_path_AOA = os.path.join(WDIR, f"{filename}_AOA.dat")
            file_path_pstat_K02 = os.path.join(WDIR, f"{filename}_static_K02.dat")
            file_path_pstat_K03 = os.path.join(WDIR, f"{filename}_static_K03.dat")
            file_path_pstat_K04 = os.path.join(WDIR, f"{filename}_static_K04.dat")
            file_path_ptot_rake = os.path.join(WDIR, f"{filename}_ptot_rake.dat")
            file_path_pstat_rake = os.path.join(WDIR, f"{filename}_pstat_rake.dat")
            file_path_GPS = os.path.join(WDIR, f"{filename}_GPS.dat")
            pickle_path_calibration = os.path.join(WDIR, f"{filename}_sensor_calibration_data.p")

            # read sensor data
            GPS = read_GPS(file_path_GPS)
            drive = read_drive(file_path_drive, t0=GPS["Time"].iloc[0])
            alphas = read_AOA_file(file_path_AOA, sigma_wall, t0=GPS["Time"].iloc[0])
            pstat_K02 = read_DLR_pressure_scanner_file(file_path_pstat_K02, n_sens=32, t0=GPS["Time"].iloc[0])
            pstat_K03 = read_DLR_pressure_scanner_file(file_path_pstat_K03, n_sens=32, t0=GPS["Time"].iloc[0])
            pstat_K04 = read_DLR_pressure_scanner_file(file_path_pstat_K04, n_sens=32, t0=GPS["Time"].iloc[0])
            ptot_rake = read_DLR_pressure_scanner_file(file_path_ptot_rake, n_sens=32, t0=GPS["Time"].iloc[0])
            pstat_rake = read_DLR_pressure_scanner_file(file_path_pstat_rake, n_sens=5, t0=GPS["Time"].iloc[0])

            # synchronize sensor data
            df_sync = synchronize_data([pstat_K02, pstat_K03, pstat_K04, ptot_rake, pstat_rake, alphas])


            if calibration_type == "file":
                # apply calibration offset from calibration file
                df_sync, l_ref = apply_calibration_offset(pickle_path_calibration, df_sync)
            elif calibration_type == "20sec":
                # apply calibration offset from first 20 seconds
                df_sync = apply_calibration_20sec(df_sync)
            elif calibration_type == "manual":
                df_sync = apply_manual_calibration(df_sync, calibration_filename)
            else:
                raise ValueError("wrong parameter 'calibration_type' passed. Either 'file', '20sec' or 'manual'")

            # append the processed data to the all_data DataFrame
            list_of_dfs.append(df_sync)

        if len(raw_data_filenames) > 1:
            df_sync = pd.concat(list_of_dfs)

        # calculate wind component
        df_sync = calc_airspeed_wind(df_sync, prandtl_data, T_air, l_ref)

        # calculate pressure coefficients
        df_p_abs = copy.deepcopy(df_sync)
        df_sync = calc_cp(df_sync, prandtl_data, pressure_data_ident_strings=['stat', 'ptot'])

        # calculate lift coefficients
        df_sync, sens_ident_cols, cp = calc_cl_cm_cdp(df_sync, df_airfoil, airfoil, flap_pivots, lambda_wall, sigma_wall, xi_wall)

        # calculate drag coefficients
        df_sync = calc_cd(df_sync, l_ref, lambda_wall, sigma_wall, xi_wall)

        #df_sync.index = df_sync.index + pd.DateOffset(hours=1)

        # visualisation
        plot_specify_segment(df_sync, df_p_abs, df_segments, U_cutoff, i_seg_plot=i_seg_plot)
        #plot_3D(df_sync_cp)
        plot_cp_x_and_wake(df_sync, df_airfoil, airfoil, sens_ident_cols, df_segments, i_seg_plot) # df_sync_cp.index.get_loc(pd.Timestamp('2024-06-13 23:38:00'))


        df_polar = prepare_polar_df(df_sync, df_segments)
        list_of_df_polars.append(df_polar)


        # Generate PolarTool polar
        Re_mean = np.around(df_polar.loc[:25, "Re"].mean() / 1e5)*1e5
        polar = at.PolarTool(name="Automobile wind tunnel", Re=Re_mean, flapangle=eta_flap, WindtunnelName="MoProMa-Car")
        polar.parseMoProMa_Polar(df_polar)
        list_of_polars.append(polar)


    # read measured polar from LWK Stuttgart, digitized with getData graph digitizer
    polarsStu = list()
    for (path_clcd, path_clalpha), eta_flap in zip(digitized_LWK_polar_paths, list_of_eta_flaps):
        polarsStu.append(at.PolarTool(name="LWK Stuttgart", Re=Re_mean, flapangle=eta_flap))
        polarsStu[-1].read_getDataGraphDigitizerPolar(path_clcd, path_clalpha)

    PPAX = dict()
    PPAX['CLmin'] = 0.0
    PPAX['CLmax'] = 1.800
    PPAX['CLdel'] = 0.5000
    PPAX['CDmin'] = 0.0000
    PPAX['CDmax'] = 0.0220
    PPAX['CDdel'] = 0.0050
    PPAX['ALmin'] = -8.0000
    PPAX['ALmax'] = 20.0000
    PPAX['ALdel'] = 2.0000
    PPAX['CMmin'] = -0.2500
    PPAX['CMmax'] = 0.000
    PPAX['CMdel'] = 0.0500

    LineAppearance = dict()

    LineAppearance['color'] = []
    LineAppearance['linestyle'] = []
    LineAppearance['marker'] = []
    # R G B
    LineAppearance['color'].append((68. / 255., 255. / 255., 68. / 255.))  # green
    LineAppearance['color'].append("k")
    LineAppearance['color'].append((60. / 255., 155. / 255., 255. / 255.))  # light blue
    LineAppearance['color'].append("k")
    LineAppearance['color'].append((255. / 255., 68. / 255., 68. / 255.))  # red
    LineAppearance['color'].append("k")
    LineAppearance['color'].append((255. / 255., 255. / 255., 68. / 255.))  # yellow
    LineAppearance['color'].append("k")
    LineAppearance['color'].append((68. / 255., 255. / 255., 68. / 255.))  # green
    LineAppearance['color'].append((68. / 255., 255. / 255., 255. / 255.))  # turquoise
    LineAppearance['color'].append((205. / 255., 55. / 255., 255. / 255.))  # purple
    LineAppearance['color'].append((255. / 255., 0. / 255., 255. / 255.))  # rose/purple

    LineAppearance['linestyle'].append("None")
    LineAppearance['linestyle'].append("-")
    LineAppearance['linestyle'].append("None")
    LineAppearance['linestyle'].append("-")
    LineAppearance['linestyle'].append("None")
    LineAppearance['linestyle'].append("-")
    LineAppearance['linestyle'].append("None")
    LineAppearance['linestyle'].append("-")


    LineAppearance['marker'].append("^")
    LineAppearance['marker'].append("^")
    LineAppearance['marker'].append("s")
    LineAppearance['marker'].append("s")
    LineAppearance['marker'].append('o')
    LineAppearance['marker'].append('o')
    LineAppearance['marker'].append('x')
    LineAppearance['marker'].append('x')

    altsort_polars = []
    for a, b in itertools.zip_longest(list_of_polars, polarsStu):
        if a:
            altsort_polars.append(a)
        if b:
            altsort_polars.append(b)

    altsort_polars[0].plotPolar(additionalPolars=altsort_polars[1:], PPAX=PPAX, Colorplot=True, LineAppearance=LineAppearance)

    #settling_time_average(df_sync_cp)

    plt.show()
    print("done")

    #plt.savefig('polar_comparison_Re1e6.jpg', format='jpg', dpi=1000)

